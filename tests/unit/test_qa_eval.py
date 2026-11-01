"""知识问答跑批与人工复核绑定测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evals.qa_review import apply_reviews, format_sheet, record_review
from evals.run_qa_eval import quality_claim_allowed, run_cases
from src.knowledge.qa import SCHEMA_VERSION, answer_binding_sha256


def _answer(question: str, *, answer_id: str = "qa-test") -> dict[str, Any]:
    """构造结构完整的最小回答包。"""
    return {
        "answer_id": answer_id,
        "schema_version": SCHEMA_VERSION,
        "question": question,
        "status": "success",
        "outcome": "supported_answer",
        "answer": "证据支持的结论。",
        "claims": [{"claim_id": "C1", "text": "结论", "evidence_ids": ["E1"], "basis": "fact"}],
        "limitations": [],
        "clarifications": [],
        "evidence": [
            {
                "evidence_id": "E1",
                "chunk_id": "chunk-1",
                "source_id": "PMID 1",
                "content_sha256": "a" * 64,
                "content": "标题：A\n摘要：Evidence.\nPMID：1",
            }
        ],
        "sources": [
            {
                "source_id": "PMID 1",
                "pmid": "1",
                "title": "A",
                "species": "human",
                "tissue": "brain",
                "technology": "scRNA-seq",
            }
        ],
        "review_status": "unreviewed",
        "review": None,
        "config": {"workspace": "knowledge_base", "lightrag_version": "test"},
        "error": None,
    }


def _case(
    case_id: str,
    question: str,
    *,
    status: str = "draft",
    source: str = "model-drafted",
) -> dict[str, Any]:
    """构造一条评测用例。"""
    return {
        "id": case_id,
        "question": question,
        "expect_outcome": "supported_answer",
        "must_support": ["支持点"],
        "forbidden": ["禁止点"],
        "status": status,
        "source": source,
    }


def _approved_cases(count: int = 12) -> list[dict[str, Any]]:
    """构造已审定的人工来源用例。"""
    return [
        _case(f"qa-{index:02d}", f"Q{index}", status="approved", source="user")
        for index in range(1, count + 1)
    ]


def _reviews(
    records: list[dict[str, Any]],
    *,
    accepted: int = 12,
    veto_index: int | None = None,
) -> list[dict[str, Any]]:
    """按记录顺序构造复核事实，前 accepted 条通过。"""
    return [
        {
            "answer_id": record["answer"]["answer_id"],
            "binding_sha256": record["binding_sha256"],
            "decision": "accepted" if index < accepted else "rejected",
            "veto": index == veto_index,
        }
        for index, record in enumerate(records)
    ]


def _runner(_client: object, question: str, **_kwargs: object) -> dict[str, Any]:
    """返回与问题对应的测试回答。"""
    return _answer(question, answer_id=f"answer-{question}")


def test_run_cases_keeps_model_drafted_report_non_quality() -> None:
    """模型初稿即使跑完也不能声明质量。"""
    cases = [_case("qa-01", "Q1"), _case("qa-02", "Q2")]

    def runner(_client: object, question: str, **_kwargs: object) -> dict[str, Any]:
        """第二条抛错，验证单例异常不中断跑批。"""
        if question == "Q2":
            raise RuntimeError("boom")
        return _answer(question)

    report = run_cases(cases, object(), qa_runner=runner)

    assert report["quality_claim_allowed"] is False
    assert report["summary"]["case_count"] == 2
    assert report["summary"]["status_counts"] == {"success": 1, "error": 1}
    assert report["cases"][1]["answer"]["error"]["stage"] == "eval"
    assert len(report["cases"][0]["binding_sha256"]) == 64


def test_run_cases_only_filters_and_rejects_unknown() -> None:
    """只跑指定用例，未知 ID 直接报错。"""
    cases = [_case("qa-01", "Q1"), _case("qa-02", "Q2")]
    report = run_cases(cases, object(), qa_runner=_runner, only=["qa-02"])
    assert [item["case_id"] for item in report["cases"]] == ["qa-02"]
    with pytest.raises(ValueError, match="未知用例"):
        run_cases(cases, object(), qa_runner=_runner, only=["qa-99"])


def test_quality_claim_requires_full_sample_and_threshold_confirmation() -> None:
    """少于 12 例或未确认阈值时不能声明质量。"""
    cases = _approved_cases()
    report = run_cases(cases, object(), qa_runner=_runner, threshold_confirmed=True)
    reviews = _reviews(report["cases"])

    assert quality_claim_allowed(cases, report["cases"], reviews) is False
    assert quality_claim_allowed(cases, report["cases"], reviews, threshold_confirmed=True) is True
    assert (
        quality_claim_allowed(cases[:1], report["cases"][:1], reviews[:1], threshold_confirmed=True)
        is False
    )


def test_quality_claim_requires_ten_accepted_and_no_veto() -> None:
    """必须达到 10/12 且否决项为 0。"""
    cases = _approved_cases()
    report = run_cases(cases, object(), qa_runner=_runner, threshold_confirmed=True)

    ten_accepted = _reviews(report["cases"], accepted=10)
    assert (
        quality_claim_allowed(cases, report["cases"], ten_accepted, threshold_confirmed=True)
        is True
    )

    vetoed = _reviews(report["cases"], accepted=10, veto_index=11)
    assert quality_claim_allowed(cases, report["cases"], vetoed, threshold_confirmed=True) is False

    stale = _reviews(report["cases"])
    stale[0]["binding_sha256"] = "0" * 64
    assert quality_claim_allowed(cases, report["cases"], stale, threshold_confirmed=True) is False


def test_binding_changes_when_evidence_content_or_applicability_changes() -> None:
    """证据原文、摘要或物种/组织/技术变化都必须改变绑定。"""
    answer = _answer("Q")
    before = answer_binding_sha256(answer)

    answer["evidence"][0]["content"] = "篡改后的证据"
    assert answer_binding_sha256(answer) != before

    answer = _answer("Q")
    answer["evidence"][0]["content_sha256"] = "b" * 64
    assert answer_binding_sha256(answer) != before

    answer = _answer("Q")
    answer["sources"][0]["species"] = "mouse"
    assert answer_binding_sha256(answer) != before


def test_quality_claim_rejects_missing_records() -> None:
    """记录数量不足时不能声明质量。"""
    cases = _approved_cases()

    assert quality_claim_allowed(cases, [], threshold_confirmed=True) is False


def _report(answer: dict[str, Any]) -> dict[str, Any]:
    """构造单例 JSON 报告。"""
    return {
        "generated_at": "2026-10-31T00:00:00+08:00",
        "quality_claim_allowed": False,
        "threshold_confirmed": False,
        "notice": "技术测试",
        "cases": [
            {
                "case_id": "qa-01",
                "case": _case("qa-01", answer["question"]),
                "answer": answer,
                "binding_sha256": answer_binding_sha256(answer),
            }
        ],
    }


def test_review_record_is_idempotent_and_conflicts_on_same_revision(tmp_path: Path) -> None:
    """同一 revision 重发幂等，不同内容冲突，veto 只允许 rejected。"""
    report = _report(_answer("Q", answer_id="qa-review-1"))
    ledger = tmp_path / "reviews.db"

    first = record_review(report, "qa-01", "accepted", "结论与证据一致", ledger=ledger)
    again = record_review(report, "qa-01", "accepted", "结论与证据一致", ledger=ledger)
    assert first["review_id"] == again["review_id"]

    with pytest.raises(ValueError, match="内容冲突"):
        record_review(report, "qa-01", "rejected", "改判", ledger=ledger)

    revision_two = record_review(
        report,
        "qa-01",
        "rejected",
        "复核后改判",
        ledger=ledger,
        revision=2,
        veto=True,
    )
    assert revision_two["source_revision"] == 2
    assert revision_two["veto"] == 1

    with pytest.raises(ValueError, match="否决项"):
        record_review(
            report,
            "qa-01",
            "accepted",
            "错误组合",
            ledger=ledger,
            revision=3,
            veto=True,
        )


def test_review_rejects_tampered_report(tmp_path: Path) -> None:
    """报告中的旧 binding 不能用于记录复核。"""
    report = _report(_answer("Q", answer_id="qa-review-2"))
    report["cases"][0]["binding_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="不一致"):
        record_review(report, "qa-01", "accepted", "理由", ledger=tmp_path / "reviews.db")


def test_apply_reviews_updates_existing_report_without_rerun(tmp_path: Path) -> None:
    """回填报告时不重跑模型，并按已确认阈值更新质量声明。"""
    cases = _approved_cases()
    report = run_cases(cases, object(), qa_runner=_runner, threshold_confirmed=True)
    ledger = tmp_path / "reviews.db"
    for index, record in enumerate(report["cases"]):
        record_review(
            report,
            record["case_id"],
            "accepted" if index < 10 else "rejected",
            "逐例复核",
            ledger=ledger,
        )

    updated = apply_reviews(report, ledger=ledger)

    assert report["quality_claim_allowed"] is False
    assert updated["quality_claim_allowed"] is True
    assert updated["summary"]["accepted_reviews"] == 10
    assert updated["summary"]["veto_reviews"] == 0
    assert updated["review_summary"][0]["binding_matches"] is True


def test_apply_reviews_rejects_tampered_answer(tmp_path: Path) -> None:
    """回填前再次校验回答绑定，拒绝被篡改的答案。"""
    report = _report(_answer("Q", answer_id="qa-apply-2"))
    ledger = tmp_path / "reviews.db"
    record_review(report, "qa-01", "accepted", "复核通过", ledger=ledger)
    report["cases"][0]["answer"]["answer"] = "篡改后的回答"

    with pytest.raises(ValueError, match="不一致"):
        apply_reviews(report, ledger=ledger)


def test_sheet_exposes_manual_checks_and_executable_report_path(tmp_path: Path) -> None:
    """审阅表包含人工检查项、证据和真实报告路径。"""
    report = _report(_answer("Q", answer_id="qa-review-3"))
    report_path = tmp_path / "report.json"
    text = format_sheet(report, report_path=report_path)

    assert "质量声明：禁止" in text
    assert "must_support" in text
    assert "支持点" in text
    assert "chunk-1" in text
    assert "a" * 64 in text
    assert str(report_path.resolve()) in text
