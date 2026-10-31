"""知识问答批量评测器：顺序跑固定问题，保存完整回答包供人工复核。

只做技术跑批，不自动判定答案正确性。只有 12 例用例全部 approved、来源不是
model-drafted、回答全部 success、人工复核无否决且至少 10 例 accepted，并且
用户显式确认阈值时，quality_claim_allowed 才为 true。

用法：
  python -m evals.run_qa_eval --out .scratch/qa_eval/report.json
  python -m evals.run_qa_eval --only qa-01 qa-02 --out .scratch/qa_eval/smoke.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.run_eval import load_cases  # noqa: E402
from src.knowledge.async_runtime import runtime as async_runtime  # noqa: E402
from src.knowledge.lightrag_client import LightRAGClient  # noqa: E402
from src.knowledge.qa import (  # noqa: E402
    SCHEMA_VERSION,
    answer_binding_sha256,
    run_knowledge_qa,
)
from src.ui.session_store import SessionStore  # noqa: E402

REPORT_SCHEMA_VERSION = "knowledge-qa-eval/1.1.0"
QUALITY_CASE_COUNT = 12
QUALITY_ACCEPTED_THRESHOLD = 10
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """返回带本地时区的秒级时间戳。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _select_cases(cases: list[dict[str, Any]], only: list[str] | None) -> list[dict[str, Any]]:
    """按用例 ID 过滤；指定未知 ID 时直接报错。"""
    if not only:
        return cases
    wanted = set(only)
    selected = [case for case in cases if case.get("id") in wanted]
    missing = wanted - {case.get("id") for case in selected}
    if missing:
        raise ValueError(f"未知用例: {', '.join(sorted(missing))}")
    return selected


def _eval_error(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    """把单例异常转成可供复核表展示的错误回答包。"""
    return {
        "answer_id": f"qa-eval-error-{uuid.uuid4().hex}",
        "schema_version": SCHEMA_VERSION,
        "question": case.get("question"),
        "status": "error",
        "outcome": None,
        "answer": f"跑批异常：{type(exc).__name__}",
        "claims": [],
        "limitations": [],
        "clarifications": [],
        "evidence": [],
        "sources": [],
        "review_status": "unreviewed",
        "review": None,
        "config": {},
        "error": {"stage": "eval", "message": f"{type(exc).__name__}: {exc}"},
    }


def latest_reviews(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """同一答案按追加顺序取最新复核；SessionStore 已按 created_at,rowid 排序。"""
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        latest[review["answer_id"]] = review
    return latest


def _current_review(
    record: dict[str, Any], latest: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """取与当前答案绑定一致的复核；旧绑定的复核视为不存在。"""
    review = latest.get((record.get("answer") or {}).get("answer_id"))
    if review and review.get("binding_sha256") == record.get("binding_sha256"):
        return review
    return None


def _accepted_review(record: dict[str, Any], latest: dict[str, dict[str, Any]]) -> bool:
    """判断当前答案是否有绑定一致且结论为 accepted 的复核。"""
    review = _current_review(record, latest)
    return bool(review and review.get("decision") == "accepted")


def review_counts(
    records: list[dict[str, Any]], latest: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """统计当前绑定有效的 accepted 与否决项数量。"""
    return {
        "accepted": sum(_accepted_review(record, latest) for record in records),
        "vetoes": sum(
            1
            for record in records
            if (review := _current_review(record, latest)) and review.get("veto")
        ),
    }


def quality_claim_allowed(
    cases: list[dict[str, Any]],
    records: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None = None,
    *,
    threshold_confirmed: bool = False,
) -> bool:
    """判断是否满足完整样本、人工阈值且无否决项的质量声明条件。"""
    if not threshold_confirmed or len(cases) != QUALITY_CASE_COUNT:
        return False
    if len(records) != len(cases):
        return False
    case_ids = {case.get("id") for case in cases}
    if {record.get("case_id") for record in records} != case_ids:
        return False
    approved = all(
        case.get("status") == "approved"
        and case.get("source") not in {"model-drafted", "model_drafted"}
        for case in cases
    )
    answered = all(record["answer"].get("status") == "success" for record in records)
    latest = latest_reviews(reviews or [])
    current = [_current_review(record, latest) for record in records]
    if any(review is None for review in current):
        return False
    accepted = sum(_accepted_review(record, latest) for record in records)
    vetoed = any(review.get("veto") for review in current if review)
    return approved and answered and accepted >= QUALITY_ACCEPTED_THRESHOLD and not vetoed


def run_cases(
    cases: list[dict[str, Any]],
    client: Any,
    *,
    qa_runner: Callable[..., dict[str, Any]] = run_knowledge_qa,
    only: list[str] | None = None,
    timeout: float | None = None,
    reviews: list[dict[str, Any]] | None = None,
    threshold_confirmed: bool = False,
) -> dict[str, Any]:
    """顺序执行用例；单例异常转成错误包，不中断其余用例。"""
    selected = _select_cases(cases, only)
    records: list[dict[str, Any]] = []
    started_all = time.monotonic()
    for case in selected:
        started = time.monotonic()
        try:
            kwargs = {"timeout": timeout} if timeout is not None else {}
            answer = qa_runner(client, case["question"], **kwargs)
            if not isinstance(answer, dict):
                raise TypeError("qa_runner 未返回 dict")
        except Exception as exc:  # noqa: BLE001 - 跑批要保留其余用例
            logger.exception("QA eval case failed: %s", case.get("id"))
            answer = _eval_error(case, exc)
        expected = case.get("expect_outcome")
        records.append(
            {
                "case_id": case.get("id"),
                "case": case,
                "answer": answer,
                "binding_sha256": answer_binding_sha256(answer),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "expected_outcome_match": (answer.get("outcome") == expected if expected else None),
                "requires_manual_review": True,
            }
        )

    outcomes = Counter(answer.get("outcome") for answer in (r["answer"] for r in records))
    statuses = Counter(answer.get("status") for answer in (r["answer"] for r in records))
    sources = Counter(case.get("source") or "unknown" for case in selected)
    comparable = [r for r in records if r["expected_outcome_match"] is not None]
    configs = [r["answer"].get("config") or {} for r in records]
    latest = latest_reviews(reviews or [])
    counts = review_counts(records, latest)
    quality_allowed = quality_claim_allowed(
        selected,
        records,
        reviews,
        threshold_confirmed=threshold_confirmed,
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "answer_schema_version": SCHEMA_VERSION,
        "quality_claim_allowed": quality_allowed,
        "threshold_confirmed": threshold_confirmed,
        "quality_threshold": {
            "cases": QUALITY_CASE_COUNT,
            "accepted": QUALITY_ACCEPTED_THRESHOLD,
            "vetoes": 0,
        },
        "notice": (
            "人工复核门槛已满足，可在报告范围内描述质量结论。"
            if quality_allowed
            else "技术跑批报告，不是质量结论。当前用例来源或状态未达到质量声明条件；"
            "必须逐例人工复核后才能引用。"
        ),
        "case_source_counts": dict(sources),
        "summary": {
            "case_count": len(records),
            "status_counts": dict(statuses),
            "outcome_counts": dict(outcomes),
            "expected_outcome_matches": sum(
                1 for record in comparable if record["expected_outcome_match"]
            ),
            "expected_outcome_comparable": len(comparable),
            "retrieval_calls": sum(int(config.get("retrieval_calls") or 0) for config in configs),
            "answer_llm_calls": sum(int(config.get("answer_llm_calls") or 0) for config in configs),
            "untraceable_chunks": sum(
                int(config.get("untraceable_chunks") or 0) for config in configs
            ),
            "accepted_reviews": counts["accepted"],
            "veto_reviews": counts["vetoes"],
            "elapsed_ms": round((time.monotonic() - started_all) * 1000),
        },
        "cases": records,
    }


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数，初始化知识库并写出 JSON 跑批报告。"""
    parser = argparse.ArgumentParser(description="Run the knowledge QA eval set.")
    parser.add_argument("--cases", default=str(ROOT / "evals" / "qa_cases.jsonl"))
    parser.add_argument("--out", required=True, help="完整报告输出路径")
    parser.add_argument("--only", nargs="*", help="只跑指定 case id")
    parser.add_argument(
        "--working-dir",
        default="knowledge_base",
        help="LightRAG 工作目录，默认仓库根目录下的 knowledge_base",
    )
    parser.add_argument("--timeout", type=float, help="单例超时秒数，默认读环境变量")
    parser.add_argument(
        "--threshold-confirmed",
        action="store_true",
        help="用户已确认阈值：12 例中至少 10 例 accepted 且否决项为 0",
    )
    parser.add_argument(
        "--reviews-db",
        default=str(ROOT / ".scratch" / "qa_eval" / "reviews.db"),
        help="人工复核账本；不存在时视为未复核，不创建",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    working_dir = Path(args.working_dir)
    if not working_dir.is_absolute():
        working_dir = ROOT / working_dir
    client = LightRAGClient(working_dir=str(working_dir))
    try:
        # 初始化失败要在第一条用例前暴露，避免 12 次重复报同一个错。
        client._initialize_rag()
        reviews_path = Path(args.reviews_db)
        reviews = (
            SessionStore(reviews_path).get_knowledge_qa_reviews() if reviews_path.exists() else []
        )
        report = run_cases(
            cases,
            client,
            only=args.only,
            timeout=args.timeout,
            reviews=reviews,
            threshold_confirmed=args.threshold_confirmed,
        )
        report["cases_path"] = str(Path(args.cases).resolve())
        report["working_dir"] = str(working_dir.resolve())
        report["workspace"] = client.workspace
        report["reviews_db"] = str(reviews_path.resolve()) if reviews_path.exists() else None
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        client.close()
        async_runtime.shutdown()

    print(
        json.dumps(
            {
                "report": str(Path(args.out).resolve()),
                "quality_claim_allowed": report["quality_claim_allowed"],
                **report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
