"""混合链路评测：工具选择、引用有效率、回答命中、延迟与 token 成本。

用法：
  python -m evals.run_knowledge_tool_eval --scripted
  python -m evals.run_knowledge_tool_eval --live --provider zhipu
  python -m evals.run_knowledge_tool_eval --scripted --baseline .scratch/eval/baseline.json

scripted 用脚本化 LLM 校验运行器与阈值；live 才代表真实模型质量。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.run_eval import load_cases  # noqa: E402

DEFAULT_CASES = ROOT / "evals" / "knowledge_tool_cases.jsonl"
REPORT_SCHEMA = "knowledge-tool-eval/1.0.0"


class ScriptedLLM:
    """按用例脚本依次返回工具调用或最终回答，并记录上下文规模。"""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        """保存脚本步骤；每调用一次 ``create`` 消耗一步。"""
        self.script = list(script)
        self.input_chars = 0
        self.output_chars = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        """模拟一次 chat completion，按脚本返回工具调用或最终文本。"""
        self.input_chars += sum(len(str(item.get("content", ""))) for item in kwargs["messages"])
        step = self.script.pop(0)
        if "tool" in step:
            call = SimpleNamespace(
                id=f"call_{step['tool']}",
                type="function",
                function=SimpleNamespace(
                    name=step["tool"],
                    arguments=json.dumps(step.get("args") or {}, ensure_ascii=False),
                ),
            )
            message = SimpleNamespace(content=None, tool_calls=[call])
        else:
            text = str(step.get("final") or "")
            self.output_chars += len(text)
            message = SimpleNamespace(content=text, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class EvalWorkflow:
    """最小工具面：检索返回证据，分析/记忆返回可继续的非终态。"""

    def __init__(self, case: dict[str, Any]) -> None:
        """保存用例开关并初始化调用记录。"""
        self.case = case
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search_knowledge_for_agent(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """按用例返回证据、空结果或服务错误。"""
        self.calls.append(("search_knowledge", {"query": query}))
        if self.case.get("retrieval_error"):
            return {
                "status": "error",
                "type": "knowledge_evidence",
                "outcome": "knowledge_unavailable",
                "message": "知识检索服务暂时不可用，请稍后重试。",
                "continue_tool_loop": True,
            }
        if self.case.get("empty_retrieval"):
            return {
                "status": "success",
                "type": "knowledge_evidence",
                "query": query,
                "evidence": [],
                "sources": [],
                "untraceable": 0,
                "evidence_count": 0,
                "retrieval_status": "no_results",
                "message": "当前知识库没有检索到相关证据。",
                "continue_tool_loop": True,
            }
        evidence_id = f"c{len(self.calls)}"
        evidence = {
            "evidence_id": evidence_id,
            "chunk_id": evidence_id,
            "title": "Eval paper",
            "content": f"{query}: evidence for evaluation.",
        }
        return {
            "status": "success",
            "type": "knowledge_evidence",
            "query": query,
            "evidence": [evidence],
            "sources": [
                {
                    "source_id": "s1",
                    "title": "Eval paper",
                    "evidence_ids": [evidence_id],
                }
            ],
            "untraceable": 0,
            "evidence_count": 1,
            "retrieval_status": "success",
            "message": "",
            "continue_tool_loop": True,
        }

    def run_analysis_for_agent(
        self, analysis_type: str, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """返回可继续工具循环的模拟分析结果。"""
        self.calls.append(("run_analysis", {"analysis_type": analysis_type}))
        return {
            "status": "success",
            "type": "analysis",
            "analysis_type": analysis_type,
            "message": "分析已完成。",
            "continue_tool_loop": True,
        }

    def query_memory_for_agent(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """返回模拟运行记忆结果。"""
        self.calls.append(("query_memory", {"query": query}))
        return {
            "status": "success",
            "type": "memory_response",
            "response": "上次参数：resolution=0.5",
            "continue_tool_loop": True,
        }

    def search_datasets_for_agent(
        self, query: str, params: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """返回模拟数据集候选。"""
        self.calls.append(("search_datasets", {"query": query}))
        return {"status": "needs_confirmation", "message": "scripted"}

    def query_knowledge_for_agent(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """返回旧知识工具结果，用于回退开关对比。"""
        self.calls.append(("query_knowledge", {"query": query}))
        return {"status": "success", "type": "knowledge_response", "response": "legacy"}

    def execute_workflow(
        self, user_input: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """LLM 不可用时的旧链路兜底。"""
        return {"status": "error", "type": "general_response", "message": "fallback"}


def _token_estimate(chars: int) -> int:
    """无真实 usage 时的保守估算：4 字符约 1 token。"""
    return max(1, chars // 4) if chars else 0


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    """在固定开启检索工具的环境下跑单条脚本用例，返回指标记录。"""
    from src.control.agent_runtime import AgentRuntime

    start = time.monotonic()
    llm = ScriptedLLM(case.get("script") or [])
    wm = EvalWorkflow(case)
    runtime = AgentRuntime(llm_client=llm, model="scripted", workflow_manager=wm)
    previous_env = os.environ.get("AGENT_KNOWLEDGE_TOOL")
    os.environ["AGENT_KNOWLEDGE_TOOL"] = "search"
    try:
        result = runtime.execute(
            case["input"],
            context={"history": []},
            knowledge_mode=case["category"] == "strict_literature",
        )
    finally:
        if previous_env is None:
            os.environ.pop("AGENT_KNOWLEDGE_TOOL", None)
        else:
            os.environ["AGENT_KNOWLEDGE_TOOL"] = previous_env
    elapsed_ms = round((time.monotonic() - start) * 1000)

    expected_tools = case.get("expect_tools") or []
    actual_tools = [name for name, _args in wm.calls if name != "fallback"]
    package = (result.get("results") or {}).get("knowledge_evidence") or {}
    citations = package.get("citations") or []
    citation_ok = (
        result.get("outcome") == "supported_answer"
        and bool(citations)
        and all(str(item).startswith("E") for item in citations)
    )
    terms = case.get("expect_terms") or []
    answer = str(result.get("message") or "")
    answer_match = all(term in answer for term in terms)
    return {
        "id": case["id"],
        "category": case["category"],
        "expected_tools": expected_tools,
        "actual_tools": actual_tools,
        "tool_selection_ok": actual_tools == expected_tools,
        "expect_citations": bool(case.get("expect_citations")),
        "citation_ok": citation_ok,
        "answer": answer,
        "answer_match": answer_match,
        "latency_ms": elapsed_ms,
        "input_tokens": _token_estimate(llm.input_chars),
        "output_tokens": _token_estimate(llm.output_chars),
        "outcome": result.get("outcome"),
        "status": result.get("status"),
    }


def _summary(records: list[dict[str, Any]], price_in: float, price_out: float) -> dict[str, Any]:
    """汇总工具选择、引用、回答命中、延迟与 token 成本。"""
    citation_cases = [record for record in records if record["expect_citations"]]
    latencies = [record["latency_ms"] for record in records] or [0]
    tokens_available = any(record["input_tokens"] is not None for record in records)
    input_tokens = sum(record["input_tokens"] or 0 for record in records)
    output_tokens = sum(record["output_tokens"] or 0 for record in records)
    return {
        "case_count": len(records),
        "tool_selection_accuracy": round(
            sum(record["tool_selection_ok"] for record in records) / len(records), 4
        )
        if records
        else 0.0,
        "citation_validity": round(
            sum(record["citation_ok"] for record in citation_cases) / len(citation_cases), 4
        )
        if citation_cases
        else None,
        "answer_match": round(sum(record["answer_match"] for record in records) / len(records), 4)
        if records
        else 0.0,
        "latency_ms_avg": round(statistics.mean(latencies), 1),
        "latency_ms_p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1),
        "input_tokens": input_tokens if tokens_available else None,
        "output_tokens": output_tokens if tokens_available else None,
        "estimated_cost_usd": round(
            input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out, 6
        )
        if tokens_available
        else None,
    }


def _live_cases(cases: list[dict[str, Any]], provider: str | None) -> list[dict[str, Any]]:
    """真实模型跑批：不注入脚本，由模型自行选择工具。"""
    import os

    if provider:
        os.environ["AGENT_LLM_PROVIDER"] = provider
    from src.config import get_current_llm
    from src.control.agent_runtime import AgentRuntime

    client, model = get_current_llm()
    records: list[dict[str, Any]] = []
    for case in cases:
        start = time.monotonic()
        wm = EvalWorkflow(case)
        runtime = AgentRuntime(llm_client=client, model=model, workflow_manager=wm)
        result = runtime.execute(
            case["input"],
            context={"history": []},
            knowledge_mode=case["category"] == "strict_literature",
        )
        elapsed_ms = round((time.monotonic() - start) * 1000)
        package = (result.get("results") or {}).get("knowledge_evidence") or {}
        answer = str(result.get("message") or "")
        actual_tools = [name for name, _args in wm.calls if name != "fallback"]
        records.append(
            {
                "id": case["id"],
                "category": case["category"],
                "expected_tools": case.get("expect_tools") or [],
                "actual_tools": actual_tools,
                "tool_selection_ok": actual_tools == (case.get("expect_tools") or []),
                "expect_citations": bool(case.get("expect_citations")),
                "citation_ok": result.get("outcome") == "supported_answer"
                and bool(package.get("citations"))
                and all(str(item).startswith("E") for item in package["citations"]),
                "answer": answer,
                "answer_match": all(term in answer for term in (case.get("expect_terms") or [])),
                "latency_ms": elapsed_ms,
                "input_tokens": None,
                "output_tokens": None,
                "outcome": result.get("outcome"),
                "status": result.get("status"),
            }
        )
    return records


def _compare_baseline(summary: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """新链路不得低于基线（2 个百分点容差）；缺字段时明确跳过。"""
    checks: dict[str, Any] = {}
    for key in ("answer_match", "citation_validity"):
        old = (baseline.get("summary") or {}).get(key)
        new = summary.get(key)
        if old is None or new is None:
            checks[key] = None
        else:
            checks[key] = {"baseline": old, "current": new, "regressed": new < old - 0.02}
    return checks


def main(argv: list[str] | None = None) -> int:
    """解析参数、跑评测并按阈值返回退出码。"""
    parser = argparse.ArgumentParser(description="CellSpatio 混合文献链路评测")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--provider", default=None)
    parser.add_argument("--baseline", default=None, help="旧链路评测报告 JSON")
    parser.add_argument("--min-selection-accuracy", type=float, default=0.9)
    parser.add_argument("--min-citation-validity", type=float, default=1.0)
    parser.add_argument("--min-answer-match", type=float, default=0.8)
    parser.add_argument("--price-input-per-mtok", type=float, default=0.0)
    parser.add_argument("--price-output-per-mtok", type=float, default=0.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    records = (
        [_run_case(case) for case in cases] if args.scripted else _live_cases(cases, args.provider)
    )
    summary = _summary(records, args.price_input_per_mtok, args.price_output_per_mtok)
    baseline = (
        json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "mode": "scripted" if args.scripted else "live",
        "summary": summary,
        "baseline_comparison": _compare_baseline(summary, baseline) if baseline else None,
        "cases": records,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")

    failed = (
        summary["tool_selection_accuracy"] < args.min_selection_accuracy
        or (summary["citation_validity"] or 0.0) < args.min_citation_validity
        or summary["answer_match"] < args.min_answer_match
    )
    if baseline:
        failed = failed or any(
            isinstance(check, dict) and check["regressed"]
            for check in report["baseline_comparison"].values()
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
