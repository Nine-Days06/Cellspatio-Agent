# evals/run_eval.py
"""工具路由 eval。

用法：
  python -m evals.run_eval --scripted    # 无网络：FakeLLM 回放金标，自检运行器
  python -m evals.run_eval --live        # 真实 LLM，输出准确率与失败明细
  python -m evals.run_eval --live --provider zhipu

金标格式（evals/cases.jsonl 每行）：
  {"id", "input", "expect_tool", "expect_args"}
  expect_tool 为 null 表示期望不调用工具（general/不支持类型直答）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _case_match(case: dict[str, Any], tool_name: str | None, tool_args: dict) -> bool:
    if tool_name != case.get("expect_tool"):
        return False
    for key, expected in (case.get("expect_args") or {}).items():
        if tool_args.get(key) != expected:
            return False
    return True


class _ReplayLLM:
    """scripted 模式：按 case 期望生成 tool-call 或文本响应。"""

    def __init__(self, case: dict[str, Any]):
        self.case = case
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        expect = self.case.get("expect_tool")
        if expect:
            args = self.case.get("expect_args") or {}
            tc = SimpleNamespace(
                id=f"call_{expect}",
                type="function",
                function=SimpleNamespace(name=expect, arguments=json.dumps(args, ensure_ascii=False)),
            )
            msg = SimpleNamespace(content=None, tool_calls=[tc])
        else:
            msg = SimpleNamespace(content="(scripted general)", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _RecordingWM:
    """记录被调用的工具名/参数；返回最小终态，避免真实副作用。"""

    def __init__(self):
        self.last: tuple[str, dict] | None = None

    def run_analysis_for_agent(self, analysis_type, params, context):
        self.last = ("run_analysis", {"analysis_type": analysis_type})
        return {"status": "needs_input", "message": "scripted"}

    def search_datasets_for_agent(self, query, params, context):
        self.last = ("search_datasets", {"query": query})
        return {"status": "needs_confirmation", "message": "scripted"}

    def query_knowledge_for_agent(self, query, context):
        self.last = ("query_knowledge", {"query": query})
        return {"status": "success", "type": "knowledge_response", "response": "scripted", "references": []}

    def execute_workflow(self, user_input, context=None):
        self.last = ("fallback", {"input": user_input})
        return {"status": "success", "type": "general_response", "message": "fallback"}


def _observed_tool(runtime, wm: _RecordingWM, case: dict[str, Any]) -> tuple[str | None, dict]:
    wm.last = None
    runtime.execute(case["input"], context={"history": []})
    if wm.last is None:
        return None, {}
    name, args = wm.last
    if name == "fallback":
        return None, {}
    return name, args


def run_scripted(cases_path: str | Path) -> dict[str, Any]:
    from src.control.agent_runtime import AgentRuntime

    cases = load_cases(cases_path)
    failures = []
    passed = 0
    for case in cases:
        llm = _ReplayLLM(case)
        wm = _RecordingWM()
        runtime = AgentRuntime(llm_client=llm, model="scripted", workflow_manager=wm)
        tool_name, tool_args = _observed_tool(runtime, wm, case)
        ok = _case_match(case, tool_name, tool_args)
        if ok:
            passed += 1
        else:
            failures.append(
                {
                    "id": case["id"],
                    "expected": case.get("expect_tool"),
                    "got": tool_name,
                    "got_args": tool_args,
                }
            )
    total = len(cases)
    return {
        "mode": "scripted",
        "total": total,
        "passed": passed,
        "failures": failures,
        "accuracy": (passed / total) if total else 0.0,
    }


def run_live(cases_path: str | Path, provider: str | None = None) -> dict[str, Any]:
    import os

    if provider:
        os.environ["AGENT_LLM_PROVIDER"] = provider
    from src.config import get_current_llm
    from src.control.agent_runtime import AgentRuntime

    try:
        client, model = get_current_llm()
    except Exception as e:  # noqa: BLE001
        return {"mode": "live", "error": f"LLM 初始化失败: {e}", "total": 0, "passed": 0, "accuracy": 0.0, "failures": []}

    cases = load_cases(cases_path)
    failures = []
    passed = 0
    for case in cases:
        wm = _RecordingWM()
        runtime = AgentRuntime(llm_client=client, model=model, workflow_manager=wm)
        tool_name, tool_args = _observed_tool(runtime, wm, case)
        if _case_match(case, tool_name, tool_args):
            passed += 1
        else:
            failures.append(
                {"id": case["id"], "expected": case.get("expect_tool"), "got": tool_name, "got_args": tool_args}
            )
    total = len(cases)
    return {
        "mode": "live",
        "model": model,
        "total": total,
        "passed": passed,
        "failures": failures,
        "accuracy": (passed / total) if total else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CellSpatio 工具路由 eval")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true", help="回放金标自检运行器")
    mode.add_argument("--live", action="store_true", help="真实 LLM 路由准确率")
    parser.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    parser.add_argument("--provider", default=None, help="live 模式覆盖 AGENT_LLM_PROVIDER")
    parser.add_argument("--min-accuracy", type=float, default=1.0, help="退出码门槛（scripted 默认须 100%）")
    args = parser.parse_args(argv)

    report = run_scripted(args.cases) if args.scripted else run_live(args.cases, args.provider)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("error"):
        return 2
    return 0 if report["accuracy"] >= args.min_accuracy else 1


if __name__ == "__main__":
    raise SystemExit(main())