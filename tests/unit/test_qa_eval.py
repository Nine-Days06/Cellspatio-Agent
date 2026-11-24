"""知识问答技术跑批测试。"""

from __future__ import annotations

from typing import Any

import pytest

from evals.run_qa_eval import REPORT_SCHEMA_VERSION, run_cases
from src.knowledge.qa import SCHEMA_VERSION


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


def _runner(_client: object, question: str, **_kwargs: object) -> dict[str, Any]:
    """返回与问题对应的测试回答。"""
    return _answer(question, answer_id=f"answer-{question}")


def test_run_cases_reports_only_technical_results() -> None:
    """跑批报告只保留执行事实，不夹带质量结论或复核绑定。"""
    cases = [_case("qa-01", "Q1"), _case("qa-02", "Q2")]

    def runner(_client: object, question: str, **_kwargs: object) -> dict[str, Any]:
        """第二条抛错，验证单例异常不中断跑批。"""
        if question == "Q2":
            raise RuntimeError("boom")
        return _answer(question)

    report = run_cases(cases, object(), qa_runner=runner)

    assert report["schema_version"] == REPORT_SCHEMA_VERSION == "knowledge-qa-eval/2.0.0"
    assert report["summary"]["case_count"] == 2
    assert report["summary"]["status_counts"] == {"success": 1, "error": 1}
    assert report["cases"][1]["answer"]["error"]["stage"] == "eval"
    assert "review_status" not in report["cases"][1]["answer"]
    assert "review" not in report["cases"][1]["answer"]
    for key in (
        "quality_claim_allowed",
        "threshold_confirmed",
        "quality_threshold",
        "notice",
        "reviews_db",
    ):
        assert key not in report
    for record in report["cases"]:
        assert "binding_sha256" not in record
        assert "requires_manual_review" not in record
    for key in ("accepted_reviews", "veto_reviews"):
        assert key not in report["summary"]


def test_run_cases_only_filters_and_rejects_unknown() -> None:
    """只跑指定用例，未知 ID 直接报错。"""
    cases = [_case("qa-01", "Q1"), _case("qa-02", "Q2")]
    report = run_cases(cases, object(), qa_runner=_runner, only=["qa-02"])
    assert [item["case_id"] for item in report["cases"]] == ["qa-02"]
    with pytest.raises(ValueError, match="未知用例"):
        run_cases(cases, object(), qa_runner=_runner, only=["qa-99"])
