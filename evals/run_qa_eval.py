"""知识问答批量评测器：顺序跑固定问题，保存完整回答包。

只做技术跑批，不自动判定答案正确性，也不提供质量声明或复核状态。

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
from src.knowledge.qa import SCHEMA_VERSION, run_knowledge_qa  # noqa: E402

REPORT_SCHEMA_VERSION = "knowledge-qa-eval/2.0.0"
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
    """把单例异常转成结构一致的错误回答包。"""
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
        "config": {},
        "error": {"stage": "eval", "message": f"{type(exc).__name__}: {exc}"},
    }


def run_cases(
    cases: list[dict[str, Any]],
    client: Any,
    *,
    qa_runner: Callable[..., dict[str, Any]] = run_knowledge_qa,
    only: list[str] | None = None,
    timeout: float | None = None,
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
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "expected_outcome_match": (answer.get("outcome") == expected if expected else None),
            }
        )

    outcomes = Counter(answer.get("outcome") for answer in (r["answer"] for r in records))
    statuses = Counter(answer.get("status") for answer in (r["answer"] for r in records))
    sources = Counter(case.get("source") or "unknown" for case in selected)
    comparable = [r for r in records if r["expected_outcome_match"] is not None]
    configs = [r["answer"].get("config") or {} for r in records]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "answer_schema_version": SCHEMA_VERSION,
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
            "elapsed_ms": round((time.monotonic() - started_all) * 1000),
        },
        "cases": records,
    }


def exit_code(report: dict[str, Any]) -> int:
    """全部用例都执行失败时返回非零，避免把基础设施故障当成成功跑批。"""
    summary = report.get("summary") or {}
    count = int(summary.get("case_count") or 0)
    errors = int((summary.get("status_counts") or {}).get("error") or 0)
    return 1 if count and errors == count else 0


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
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    working_dir = Path(args.working_dir)
    if not working_dir.is_absolute():
        working_dir = ROOT / working_dir
    client = LightRAGClient(working_dir=str(working_dir))
    try:
        # 初始化失败要在第一条用例前暴露，避免逐例重复报同一个错。
        client._initialize_rag()
        report = run_cases(
            cases,
            client,
            only=args.only,
            timeout=args.timeout,
        )
        report["cases_path"] = str(Path(args.cases).resolve())
        report["working_dir"] = str(working_dir.resolve())
        report["workspace"] = client.workspace
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
                **report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
