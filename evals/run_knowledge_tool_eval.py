"""混合链路评测：脚本契约、工具选择、真实端到端三层分开跑。

用法：
  python -m evals.run_knowledge_tool_eval --scripted
  python -m evals.run_knowledge_tool_eval --tools --provider zhipu
  python -m evals.run_knowledge_tool_eval --e2e --provider zhipu
  python -m evals.run_knowledge_tool_eval --e2e --baseline .scratch/eval/baseline.json

三层可信度不同：
- scripted：脚本化 LLM + 假工作流，只验证运行器契约与阈值。
- tools（旧 --live 为弃用别名）：真实模型 + 假工作流，只报告工具选择与格式。
- e2e：真实模型 + 真实 WorkflowManager + 真实检索，才能支撑质量结论。
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
REPORT_SCHEMA = "knowledge-tool-eval/2.0.0"
MODE_SCRIPTED = "scripted"
MODE_TOOL_SELECTION = "tool_selection"
MODE_E2E = "e2e"
E2E_CATEGORIES = frozenset({"literature", "strict_literature"})
LAYER_NOTES = {
    MODE_SCRIPTED: "脚本化 LLM + 假工作流，仅验证运行器契约与阈值",
    MODE_TOOL_SELECTION: "真实模型 + 假工作流，仅报告工具选择与格式指标",
    MODE_E2E: "真实模型 + 真实 WorkflowManager + 真实检索，可支撑质量结论",
}


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


def _case_record(
    case: dict[str, Any],
    actual_tools: list[str] | None,
    result: dict[str, Any],
    elapsed_ms: int,
    input_tokens: int | None,
    output_tokens: int | None,
    *,
    report_answer: bool = True,
) -> dict[str, Any]:
    """把一次运行整理为统一指标记录；无工具记录时工具选择记为未知。"""
    expected_tools = case.get("expect_tools") or []
    package = (result.get("results") or {}).get("knowledge_evidence") or {}
    citations = package.get("citations") or []
    citation_ok = (
        result.get("outcome") == "supported_answer"
        and bool(citations)
        and all(str(item).startswith("E") for item in citations)
    )
    terms = case.get("expect_terms") or []
    answer = str(result.get("message") or "")
    return {
        "id": case["id"],
        "category": case["category"],
        "expected_tools": expected_tools,
        "actual_tools": actual_tools,
        "tool_selection_ok": None if actual_tools is None else actual_tools == expected_tools,
        "expect_citations": bool(case.get("expect_citations")),
        "citation_ok": citation_ok,
        "answer": answer if report_answer else None,
        "answer_match": all(term in answer for term in terms) if report_answer else None,
        "latency_ms": elapsed_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "outcome": result.get("outcome"),
        "status": result.get("status"),
    }


def _run_scripted_case(case: dict[str, Any]) -> dict[str, Any]:
    """在固定开启检索工具的环境下跑单条脚本用例，返回指标记录。"""
    from src.control.agent_runtime import AgentRuntime

    start = time.monotonic()
    llm = ScriptedLLM(case.get("script") or [])
    wm = EvalWorkflow(case)
    runtime = AgentRuntime(llm_client=llm, model="scripted", workflow_manager=wm)
    previous = _pin_knowledge_tool()
    try:
        result = runtime.execute(
            case["input"],
            context={"history": []},
            knowledge_mode=case["category"] == "strict_literature",
        )
    finally:
        _restore_knowledge_tool(previous)
    return _case_record(
        case,
        [name for name, _args in wm.calls if name != "fallback"],
        result,
        round((time.monotonic() - start) * 1000),
        _token_estimate(llm.input_chars),
        _token_estimate(llm.output_chars),
    )


def _summary(
    records: list[dict[str, Any]], price_in: float, price_out: float, layer: str
) -> dict[str, Any]:
    """按评测层汇总；假工作流的层不声明回答质量。"""
    selection_records = [record for record in records if record["tool_selection_ok"] is not None]
    answer_records = [record for record in records if record["answer_match"] is not None]
    citation_cases = [record for record in records if record["expect_citations"]]
    latencies = [record["latency_ms"] for record in records] or [0]
    tokens_available = any(record["input_tokens"] is not None for record in records)
    input_tokens = sum(record["input_tokens"] or 0 for record in records)
    output_tokens = sum(record["output_tokens"] or 0 for record in records)
    return {
        "case_count": len(records),
        "tool_selection_accuracy": round(
            sum(record["tool_selection_ok"] for record in selection_records)
            / len(selection_records),
            4,
        )
        if selection_records
        else None,
        "citation_validity": round(
            sum(record["citation_ok"] for record in citation_cases) / len(citation_cases), 4
        )
        if citation_cases
        else None,
        "answer_match": (
            None
            if layer == MODE_TOOL_SELECTION
            else round(
                sum(record["answer_match"] for record in answer_records) / len(answer_records),
                4,
            )
            if answer_records
            else None
        ),
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


def _require_real_workflow(wm: Any) -> None:
    """端到端层必须使用真实 WorkflowManager；误退回假工作流立即失败。"""
    from src.control.workflow_manager import WorkflowManager

    if type(wm) is not WorkflowManager:
        raise RuntimeError("端到端评测必须使用真实 WorkflowManager，禁止回落到假工作流")


def _use_provider(provider: str | None) -> None:
    """按参数切换 LLM 供应商，保持与生产一致的配置读取。"""
    if provider:
        os.environ["AGENT_LLM_PROVIDER"] = provider


def _pin_knowledge_tool() -> str | None:
    """评测固定走 search_knowledge，返回原值供调用方恢复。"""
    previous = os.environ.get("AGENT_KNOWLEDGE_TOOL")
    os.environ["AGENT_KNOWLEDGE_TOOL"] = "search"
    return previous


def _restore_knowledge_tool(previous: str | None) -> None:
    """恢复调用前的检索工具开关，避免污染进程环境。"""
    if previous is None:
        os.environ.pop("AGENT_KNOWLEDGE_TOOL", None)
    else:
        os.environ["AGENT_KNOWLEDGE_TOOL"] = previous


def _tool_selection_cases(
    cases: list[dict[str, Any]], provider: str | None
) -> list[dict[str, Any]]:
    """真实模型 + 假工作流：只观察工具选择与格式，不声明回答质量。"""
    _use_provider(provider)
    from src.config import get_current_llm
    from src.control.agent_runtime import AgentRuntime

    client, model = get_current_llm()
    records: list[dict[str, Any]] = []
    previous = _pin_knowledge_tool()
    try:
        for case in cases:
            start = time.monotonic()
            wm = EvalWorkflow(case)
            runtime = AgentRuntime(llm_client=client, model=model, workflow_manager=wm)
            result = runtime.execute(
                case["input"],
                context={"history": []},
                knowledge_mode=case["category"] == "strict_literature",
            )
            records.append(
                _case_record(
                    case,
                    [name for name, _args in wm.calls if name != "fallback"],
                    result,
                    round((time.monotonic() - start) * 1000),
                    None,
                    None,
                    report_answer=False,
                )
            )
    finally:
        _restore_knowledge_tool(previous)
    return records


def _build_e2e_workflow(provider: str | None, knowledge_dir: str) -> Any:
    """按生产配置构造真实 WorkflowManager；用例只触达检索入口。"""
    from src.control.workflow_manager import WorkflowManager
    from src.knowledge.lightrag_client import LightRAGClient

    return WorkflowManager(
        intent_parser=None,
        knowledge_client=LightRAGClient(
            working_dir=knowledge_dir,
            config={"provider": provider} if provider else {},
        ),
        r_executor=None,
        visualizer=None,
    )


def _e2e_cases(
    cases: list[dict[str, Any]],
    provider: str | None,
    knowledge_dir: str,
    workflow_factory: Any = None,
) -> list[dict[str, Any]]:
    """真实模型 + 真实 WorkflowManager + 真实检索，产出可对外声明的质量指标。"""
    _use_provider(provider)
    from src.config import get_current_llm
    from src.control.agent_runtime import AgentRuntime

    factory = workflow_factory or _build_e2e_workflow
    wm = factory(provider, knowledge_dir)
    _require_real_workflow(wm)
    selected = [
        case
        for case in cases
        if case["category"] in E2E_CATEGORIES
        and not case.get("empty_retrieval")
        and not case.get("retrieval_error")
    ]
    if not selected:
        raise RuntimeError("端到端评测没有可用用例：需要 literature/strict_literature")
    client, model = get_current_llm()
    records: list[dict[str, Any]] = []
    previous = _pin_knowledge_tool()
    try:
        for case in selected:
            start = time.monotonic()
            runtime = AgentRuntime(llm_client=client, model=model, workflow_manager=wm)
            result = runtime.execute(
                case["input"],
                context={"history": []},
                # 端到端按用例走默认/严格路径；literature 不再被强制成严格模式。
                knowledge_mode=case["category"] == "strict_literature",
            )
            records.append(
                _case_record(
                    case,
                    None,
                    result,
                    round((time.monotonic() - start) * 1000),
                    None,
                    None,
                )
            )
    finally:
        _restore_knowledge_tool(previous)
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
    mode.add_argument("--scripted", action="store_true", help="脚本契约层")
    mode.add_argument(
        "--tools",
        "--live",
        dest="tools",
        action="store_true",
        help="工具选择层（真实模型 + 假工作流）；--live 为弃用别名",
    )
    mode.add_argument("--e2e", action="store_true", help="真实端到端层")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--provider", default=None)
    parser.add_argument(
        "--knowledge-dir", default=os.environ.get("KNOWLEDGE_DIR", "./knowledge_base")
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="端到端层基线报告 JSON（其他层传入会报错；版本需匹配）",
    )
    parser.add_argument("--min-selection-accuracy", type=float, default=0.9)
    parser.add_argument("--min-citation-validity", type=float, default=1.0)
    parser.add_argument("--min-answer-match", type=float, default=0.8)
    parser.add_argument("--price-input-per-mtok", type=float, default=0.0)
    parser.add_argument("--price-output-per-mtok", type=float, default=0.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    if args.scripted:
        layer = MODE_SCRIPTED
    elif args.tools:
        layer = MODE_TOOL_SELECTION
    else:
        layer = MODE_E2E
    baseline = (
        json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    )
    if baseline is not None:
        if layer != MODE_E2E:
            parser.error("基线比较只在 --e2e 层生效")
        if baseline.get("mode") != MODE_E2E:
            parser.error("基线报告不是端到端层（mode 必须为 e2e），拒绝比较")
        if baseline.get("schema_version") != REPORT_SCHEMA:
            parser.error(
                f"基线报告版本不匹配：需要 {REPORT_SCHEMA}，实际为 {baseline.get('schema_version')}"
            )
    cases = load_cases(args.cases)
    if not cases:
        parser.error(f"用例集为空：{args.cases}")
    if layer == MODE_SCRIPTED:
        records = [_run_scripted_case(case) for case in cases]
    elif layer == MODE_TOOL_SELECTION:
        records = _tool_selection_cases(cases, args.provider)
    else:
        records = _e2e_cases(cases, args.provider, args.knowledge_dir)
    summary = _summary(records, args.price_input_per_mtok, args.price_output_per_mtok, layer)
    report = {
        "schema_version": REPORT_SCHEMA,
        "mode": layer,
        "layer_note": LAYER_NOTES[layer],
        "baseline_comparable": layer == MODE_E2E,
        "summary": summary,
        "baseline_comparison": _compare_baseline(summary, baseline) if baseline else None,
        "cases": records,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")

    selection = summary["tool_selection_accuracy"]
    citation = summary["citation_validity"]
    answer = summary["answer_match"]
    if layer == MODE_TOOL_SELECTION:
        failed = (selection or 0.0) < args.min_selection_accuracy or (
            citation or 0.0
        ) < args.min_citation_validity
    else:
        failed = (
            (selection is not None and selection < args.min_selection_accuracy)
            or (citation or 0.0) < args.min_citation_validity
            or (answer is not None and answer < args.min_answer_match)
        )
    if baseline:
        failed = failed or any(
            isinstance(check, dict) and check["regressed"]
            for check in report["baseline_comparison"].values()
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
