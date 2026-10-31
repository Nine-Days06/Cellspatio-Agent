"""知识问答人工复核：生成审阅表，按回答绑定追加复核事实。

用法：
  python -m evals.qa_review sheet --report .scratch/qa_eval/report.json
  python -m evals.qa_review record --report .scratch/qa_eval/report.json \
      --case qa-01 --decision accepted --reason "证据与结论一致"
  python -m evals.qa_review list --answer-id qa-...
  python -m evals.qa_review apply --report .scratch/qa_eval/report.json \
      --out .scratch/qa_eval/report.reviewed.json
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.run_qa_eval import latest_reviews, quality_claim_allowed, review_counts  # noqa: E402
from src.knowledge.qa import answer_binding_sha256  # noqa: E402
from src.ui.session_store import SessionStore  # noqa: E402

DEFAULT_LEDGER = ROOT / ".scratch" / "qa_eval" / "reviews.db"


def _load_report(path: str | Path) -> dict[str, Any]:
    """读取 UTF-8 JSON 跑批报告。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _find_record(report: dict[str, Any], case_id: str) -> dict[str, Any]:
    """按用例 ID 查找报告中的单例记录。"""
    for record in report.get("cases") or []:
        if record.get("case_id") == case_id or (record.get("case") or {}).get("id") == case_id:
            return record
    raise ValueError(f"报告中找不到用例: {case_id}")


def _bullet_list(values: list[str] | None) -> str:
    """把检查项渲染成 Markdown 列表，空值显示为“无”。"""
    if not values:
        return "- 无"
    return "\n".join(f"- {value}" for value in values)


def format_sheet(report: dict[str, Any], *, report_path: str | Path | None = None) -> str:
    """把报告转成便于逐例人工审阅的 Markdown。"""
    command_report = Path(report_path).resolve() if report_path else "REPORT"
    lines = [
        "# 知识问答人工复核表",
        "",
        f"- 报告生成时间：{report.get('generated_at')}",
        f"- 质量声明：{'允许' if report.get('quality_claim_allowed') else '禁止'}",
        f"- 说明：{report.get('notice') or ''}",
    ]
    for record in report.get("cases") or []:
        case = record.get("case") or {}
        answer = record.get("answer") or {}
        case_id = record.get("case_id") or case.get("id")
        lines += [
            "",
            f"## {case_id}",
            "",
            f"- 问题：{case.get('question')}",
            f"- 预期 outcome：{case.get('expect_outcome')}",
            f"- 实际 outcome：{answer.get('outcome')}",
            f"- 状态：{answer.get('status')}",
            f"- 绑定哈希：`{record.get('binding_sha256')}`",
            "",
            "**回答**",
            "",
            str(answer.get("answer") or ""),
            "",
            "**Claims**",
            "",
        ]
        claims = answer.get("claims") or []
        if claims:
            for claim in claims:
                lines.append(
                    f"- {claim.get('claim_id')}: {claim.get('text')} "
                    f"（{claim.get('basis')}; {', '.join(claim.get('evidence_ids') or [])}）"
                )
        else:
            lines.append("- 无")
        lines += ["", "**人工检查项（必须逐条判断）**", "", "must_support:", ""]
        lines.append(_bullet_list(case.get("must_support")))
        lines += ["", "forbidden:", "", _bullet_list(case.get("forbidden")), "", "**Evidence**", ""]
        evidence = answer.get("evidence") or []
        if evidence:
            for item in evidence:
                lines += [
                    f"- {item.get('evidence_id')} | {item.get('source_id')} | "
                    f"chunk={item.get('chunk_id')} | sha256={item.get('content_sha256')}",
                    "",
                    str(item.get("content") or ""),
                    "",
                ]
        else:
            lines += ["- 无", ""]
        lines += ["**Sources**", ""]
        sources = answer.get("sources") or []
        if sources:
            for item in sources:
                lines.append(
                    f"- {item.get('source_id')} | PMID={item.get('pmid')} | "
                    f"DOI={item.get('doi')} | {item.get('title')} | {item.get('url')}"
                )
        else:
            lines.append("- 无")
        if answer.get("error"):
            lines += ["", f"**Error**：{answer.get('error')}"]
        config = answer.get("config") or {}
        if config.get("generation_error"):
            lines += ["", f"**Generation**：{config.get('generation_error')}"]
        if config.get("validation_error"):
            lines += [
                "",
                f"**Validation**：{config.get('validation_outcome')} / "
                f"{config.get('validation_claim_count')} claims / "
                f"{config.get('validation_error')}",
            ]
        lines += [
            "",
            "复核命令：",
            "",
            "```bash",
            (
                f'python -m evals.qa_review record --report "{command_report}" '
                f'--case {case_id} --decision accepted --reason "..."'
            ),
            "```",
            "",
        ]
    return "\n".join(lines)


def record_review(
    report: dict[str, Any],
    case_id: str,
    decision: str,
    reason: str,
    *,
    ledger: str | Path,
    reviewer: str = "user",
    revision: int = 1,
    veto: bool = False,
) -> dict[str, Any]:
    """校验报告绑定后追加复核事实。"""
    record = _find_record(report, case_id)
    answer = record.get("answer") or {}
    binding = answer_binding_sha256(answer)
    if record.get("binding_sha256") != binding:
        raise ValueError("报告中的 answer 与 binding_sha256 不一致，拒绝记录")
    answer_id = answer.get("answer_id")
    if not answer_id:
        raise ValueError("回答包缺少 answer_id")
    store = SessionStore(ledger)
    return store.record_knowledge_qa_review(
        answer_id=answer_id,
        binding_sha256=binding,
        record_version=str(answer.get("schema_version") or "unknown"),
        source_revision=revision,
        decision=decision,
        reason=reason,
        reviewer=reviewer,
        veto=veto,
    )


def apply_reviews(
    report: dict[str, Any],
    *,
    ledger: str | Path,
    threshold_confirmed: bool | None = None,
) -> dict[str, Any]:
    """把已落库的最新复核绑定回已有报告，不重新调用问答模型。"""
    ledger = Path(ledger)
    if not ledger.exists():
        raise ValueError(f"复核账本不存在: {ledger}")
    reviews = SessionStore(ledger).get_knowledge_qa_reviews()
    records = report.get("cases") or []
    for record in records:
        answer = record.get("answer") or {}
        if record.get("binding_sha256") != answer_binding_sha256(answer):
            raise ValueError(f"报告中的 answer 与 binding_sha256 不一致: {record.get('case_id')}")
    updated = copy.deepcopy(report)
    confirmed = (
        bool(updated.get("threshold_confirmed"))
        if threshold_confirmed is None
        else threshold_confirmed
    )
    updated["threshold_confirmed"] = confirmed
    updated["quality_claim_allowed"] = quality_claim_allowed(
        [record.get("case") or {} for record in records],
        records,
        reviews,
        threshold_confirmed=confirmed,
    )
    updated["reviews_db"] = str(ledger.resolve())
    latest = latest_reviews(reviews)
    counts = review_counts(records, latest)
    updated.setdefault("summary", {})["accepted_reviews"] = counts["accepted"]
    updated["summary"]["veto_reviews"] = counts["vetoes"]
    updated["review_summary"] = []
    for record in records:
        answer_id = (record.get("answer") or {}).get("answer_id")
        review = latest.get(answer_id)
        updated["review_summary"].append(
            {
                "case_id": record.get("case_id"),
                "answer_id": answer_id,
                "decision": review.get("decision") if review else None,
                "binding_matches": bool(review)
                and review.get("binding_sha256") == record.get("binding_sha256"),
                "veto": bool(review.get("veto")) if review else None,
                "reason": review.get("reason") if review else None,
                "reviewer": review.get("reviewer") if review else None,
            }
        )
    return updated


def main(argv: list[str] | None = None) -> int:
    """执行审阅表、复核记录或报告回填子命令。"""
    parser = argparse.ArgumentParser(description="Knowledge QA human review tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    sheet = sub.add_parser("sheet", help="生成人工审阅 Markdown")
    sheet.add_argument("--report", required=True)
    sheet.add_argument("--out", help="写入文件；不传则打印到 stdout")

    record = sub.add_parser("record", help="记录一条人工复核")
    record.add_argument("--report", required=True)
    record.add_argument("--case", required=True)
    record.add_argument("--decision", choices=["accepted", "rejected"], required=True)
    record.add_argument("--reason", required=True)
    record.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    record.add_argument("--reviewer", default="user")
    record.add_argument("--revision", type=int, default=1)
    record.add_argument("--veto", action="store_true", help="标记为否决项，必须同时 rejected")

    listing = sub.add_parser("list", help="列出复核事实")
    listing.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    listing.add_argument("--answer-id")

    apply = sub.add_parser("apply", help="把复核绑定回已有报告，不重跑问答")
    apply.add_argument("--report", required=True)
    apply.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    apply.add_argument("--out", required=True)
    apply.add_argument(
        "--threshold-confirmed",
        action="store_true",
        default=None,
        help="覆盖报告中的阈值确认状态",
    )

    args = parser.parse_args(argv)
    if args.command == "sheet":
        text = format_sheet(_load_report(args.report), report_path=args.report)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0
    if args.command == "record":
        review = record_review(
            _load_report(args.report),
            args.case,
            args.decision,
            args.reason,
            ledger=args.ledger,
            reviewer=args.reviewer,
            revision=args.revision,
            veto=args.veto,
        )
        print(json.dumps(review, ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply":
        report = apply_reviews(
            _load_report(args.report),
            ledger=args.ledger,
            threshold_confirmed=args.threshold_confirmed,
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "report": str(out.resolve()),
                    "quality_claim_allowed": report["quality_claim_allowed"],
                    "review_summary": report["review_summary"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    store = SessionStore(args.ledger)
    print(json.dumps(store.get_knowledge_qa_reviews(args.answer_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
