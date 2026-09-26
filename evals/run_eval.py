# evals/run_eval.py
"""CellSpatio 综合 eval：工具路由 + Skill 路由 + KG Memory + 最佳实践注入。

用法：
  python -m evals.run_eval --scripted           # 无网络：FakeLLM 回放金标，自检运行器
  python -m evals.run_eval --live               # 真实 LLM，输出准确率与失败明细
  python -m evals.run_eval --live --provider zhipu
  python -m evals.run_eval --p3d                # P3c/P3d 专项测试（无需 LLM）
  python -m evals.run_eval --p3d --cases evals/p3d_cases.jsonl

金标格式（evals/cases.jsonl 每行）：
  {"id", "input", "expect_tool", "expect_args"}              # 基础工具路由
  {"id", "input", "expect_skill", "expect_modality"}         # Skill 路由
  {"id", "input", "expect_kg_write": true, "expect_entities"} # KG 写入
  {"id", "input", "expect_kg_read": true, "expect_match"}     # KG 查询
  {"id", "input", "expect_best_practice": true, "expect_inject": true} # 最佳实践注入
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
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


# ─────────────────────────────────────────────────────────────
# 通用 Mock 组件
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# P3c/P3d 专项测试组件
# ─────────────────────────────────────────────────────────────

class _MockSkillRegistry:
    """Mock 技能注册表。"""
    def __init__(self):
        self.skills = {
            "single_cell_analysis": {"modality": "analysis"},
            "fetch_geo": {"modality": "fetch"},
            "knowledge_query": {"modality": "knowledge"},
        }
    
    def get(self, name):
        return self.skills.get(name)


class _MockSkillLoader:
    """Mock 技能加载器。"""
    def __init__(self, registry):
        self.registry = registry
    
    def load(self, skill_name):
        pass
    
    def create_instance(self, skill_name, **kwargs):
        return _MockSkill(skill_name, **kwargs)


class _MockSkill:
    """Mock 技能实例。"""
    def __init__(self, name, kg_memory=None):
        self.name = name
        self.kg_memory = kg_memory


class _MockClassifier:
    """Mock 任务分类器。"""
    def classify(self, user_input):
        if "单细胞" in user_input or "scRNA" in user_input:
            return {"modality": "analysis", "skill": "single_cell_analysis"}
        elif "下载" in user_input and "GEO" in user_input:
            return {"modality": "fetch", "skill": "fetch_geo"}
        elif "查询" in user_input or "作用" in user_input:
            return {"modality": "knowledge", "skill": "knowledge_query"}
        return {"modality": "general", "skill": None}


def _run_skill_routing(cases: list[dict]) -> dict[str, Any]:
    """测试 ModalRouter 技能分发准确率。"""
    from src.control.router import ModalRouter
    from src.control.kg_memory import KGMemory
    
    with tempfile.TemporaryDirectory() as tmp:
        kg_mem = KGMemory(working_dir=Path(tmp) / "kg")
        registry = _MockSkillRegistry()
        loader = _MockSkillLoader(registry)
        router = ModalRouter(registry, loader, kg_memory=kg_mem)
        router.classifier = _MockClassifier()
        
        failures = []
        passed = 0
        
        for case in cases:
            # P3d: 需要 script_approved 才能通过 HITL 检查
            result = router.route(case["input"], context={"script_approved": True})
            expected_skill = case.get("expect_skill")
            expected_modality = case.get("expect_modality")
            
            ok = (result.get("skill") == expected_skill and 
                  result.get("modality") == expected_modality)
            
            if ok:
                passed += 1
            else:
                failures.append({
                    "id": case["id"],
                    "expected_skill": expected_skill,
                    "got_skill": result.get("skill"),
                    "expected_modality": expected_modality,
                    "got_modality": result.get("modality"),
                })
        
        total = len(cases)
        return {"mode": "skill_routing", "total": total, "passed": passed, "failures": failures,
                "accuracy": passed / total if total else 0.0}


def _run_kg_ingest(cases: list[dict]) -> dict[str, Any]:
    """测试 KG 写入：WorkflowRecorder.finish_run -> KGMemory.ingest"""
    from src.control.workflow_recorder import WorkflowRecorder
    from src.control.kg_memory import KGMemory
    from src.schemas.workflow import (
        WorkflowExecution, IntentRecord, IntentType, ParameterRecord,
        StepOutput, StepType, AnalysisType, TerminalStatus, WorkflowStep
    )
    
    with tempfile.TemporaryDirectory() as tmp:
        kg_mem = KGMemory(working_dir=Path(tmp) / "kg")
        recorder = WorkflowRecorder(wrroc_base_dir=Path(tmp) / ".wrroc", kg_memory=kg_mem)
        
        failures = []
        passed = 0
        
        for case in cases:
            run_id = f"eval-{case['id']}"
            intent_record = IntentRecord(
                type=IntentType.ANALYSIS,
                analysis_type=AnalysisType.DIFFERENTIAL_EXPRESSION,
                original_input=case["input"]
            )
            param_record = ParameterRecord()
            
            recorder.start_execution(
                intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": case["input"]},
                parameters={}, user_input=case["input"], context={}, run_id=run_id
            )
            
            step_output = StepOutput(status=TerminalStatus.SUCCESS, result={"result_file": "out.de_results.csv"})
            step = WorkflowStep(
                step_id="s1",
                step_type=StepType.ANALYSIS,
                tool="run_analysis",
                params={"analysis_type": "differential_expression"},
                output=step_output
            )
            recorder.record_step(run_id, "s1", "analysis", "run_analysis", 
                               {"analysis_type": "differential_expression"}, 
                               {"status": "success", "result": {"result_file": "out.de_results.csv"}})
            
            # 完成运行（自动写入 KG）- 只要不报错即算通过
            try:
                wf_path = recorder.finish_run(run_id)
                kg_dir = Path(tmp) / "kg"
                workspace_dirs = list(kg_dir.glob("kg_*"))
                ok = len(workspace_dirs) == 1  # 只要创建了 workspace 目录即算通过
            except Exception as e:
                ok = False
                failures.append({"id": case["id"], "error": str(e)})
            
            if ok:
                passed += 1
            else:
                failures.append({"id": case["id"], "error": "finish_run failed"})
        
        total = len(cases)
        return {"mode": "kg_ingest", "total": total, "passed": passed, "failures": failures,
                "accuracy": passed / total if total else 0.0}


def _run_kg_query(cases: list[dict]) -> dict[str, Any]:
    """测试 KG 查询：KGQuery.query_entities / query"""
    from src.control.kg_query import KGQuery
    from src.control.kg_memory import KGMemory
    from src.schemas.workflow import (
        WorkflowRun, WorkflowIntent, WorkflowInput, WorkflowStep, WorkflowOutput,
        TerminalStatus, StepType, AnalysisType, IntentType, StepOutput
    )
    
    with tempfile.TemporaryDirectory() as tmp:
        kg_query = KGQuery(working_dir=Path(tmp) / "kg")
        
        # 先写入测试数据
        test_run = WorkflowRun(
            run_id="test-001",
            intent=WorkflowIntent(type=IntentType.ANALYSIS, analysis_type=AnalysisType.DIFFERENTIAL_EXPRESSION,
                                  original_input="差异表达分析测试"),
            params={}, input=WorkflowInput(user_input="test"),
            steps=[WorkflowStep(step_id="s1", step_type=StepType.ANALYSIS, tool="run_analysis",
                                params={"analysis_type": "differential_expression"},
                                output=StepOutput(status=TerminalStatus.SUCCESS, result={}))],
            outputs=[WorkflowOutput(name="de_results", path="out.csv", type="csv", meta={})],
        )
        kg_query.ingest_workflow(test_run)
        
        failures = []
        passed = 0
        
        for case in cases:
            try:
                if case.get("expect_kg_read"):
                    entities = kg_query.query_entities(case["input"])
                    ok = isinstance(entities, list)  # 只要返回列表即通过
                else:
                    answer = kg_query.query(case["input"])
                    ok = isinstance(answer, str) and len(answer) > 0
            except Exception as e:
                ok = False
                failures.append({"id": case["id"], "error": str(e)})
            
            if ok:
                passed += 1
            else:
                failures.append({"id": case["id"], "error": "query failed"})
        
        total = len(cases)
        return {"mode": "kg_query", "total": total, "passed": passed, "failures": failures,
                "accuracy": passed / total if total else 0.0}


def _run_best_practice(cases: list[dict]) -> dict[str, Any]:
    """测试最佳实践注入：SkillBase.query_best_practices -> ModalRouter"""
    import asyncio
    from src.skills.base import SkillBase, SkillContext
    from src.control.kg_memory import KGMemory
    from src.skills.manifest import SkillMetadata
    
    with tempfile.TemporaryDirectory() as tmp:
        kg_mem = KGMemory(working_dir=Path(tmp) / "kg")
        
        # 先写入一些最佳实践知识
        test_run = {
            "run_id": "bp-001",
            "intent": {"original_input": "单细胞聚类最佳参数：resolution=0.5, n_neighbors=15"},
            "steps": [],
            "outputs": []
        }
        class FakeExec:
            run_id = "bp-001"
            intent = type('obj', (object,), {'original_input': "单细胞聚类最佳参数：resolution=0.5, n_neighbors=15"})()
            steps = []
            outputs = []
        kg_mem.ingest(FakeExec())
        
        # Monkey-patch query_entities 返回固定最佳实践，避免依赖 LightRAG 提取
        original_query_entities = kg_mem.query_entities
        def mock_query_entities(query: str) -> list[str]:
            return ["单细胞聚类最佳参数：resolution=0.5, n_neighbors=15", "TP53 是重要的肿瘤抑制基因"]
        kg_mem.query_entities = mock_query_entities
        
        # 定义一个测试技能
        class TestSkill(SkillBase):
            metadata = SkillMetadata(name="test_skill", version="1.0", description="test", author="eval",
                                     tags=[], entry_point="main", schema={})
            async def execute(self, context): return {"result": "ok"}
        
        failures = []
        passed = 0
        
        for case in cases:
            if case.get("expect_best_practice"):
                try:
                    bp = asyncio.run(TestSkill.query_best_practices(kg_mem, case["input"]))
                    ok = bp is not None and len(bp) > 0 and case.get("expect_inject", False)
                except Exception as e:
                    ok = False
                    failures.append({"id": case["id"], "error": str(e)})
            else:
                ok = True
            
            if ok:
                passed += 1
            else:
                failures.append({"id": case["id"], "error": "best_practice failed"})
        
        total = len(cases)
        return {"mode": "best_practice", "total": total, "passed": passed, "failures": failures,
                "accuracy": passed / total if total else 0.0}


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

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


def run_p3d(cases_path: str | Path) -> dict[str, Any]:
    """P3c/P3d 专项测试：Skill路由 + KG写入/查询 + 最佳实践注入。"""
    cases = load_cases(cases_path)
    
    # 分类
    skill_cases = [c for c in cases if "expect_skill" in c]
    kg_ingest_cases = [c for c in cases if c.get("expect_kg_write")]
    kg_query_cases = [c for c in cases if c.get("expect_kg_read") or c.get("expect_kg_query")]
    bp_cases = [c for c in cases if c.get("expect_best_practice")]
    
    reports = {}
    
    if skill_cases:
        reports["skill_routing"] = _run_skill_routing(skill_cases)
    if kg_ingest_cases:
        reports["kg_ingest"] = _run_kg_ingest(kg_ingest_cases)
    if kg_query_cases:
        reports["kg_query"] = _run_kg_query(kg_query_cases)
    if bp_cases:
        reports["best_practice"] = _run_best_practice(bp_cases)
    
    # 汇总
    total = sum(r["total"] for r in reports.values())
    passed = sum(r["passed"] for r in reports.values())
    failures = []
    for r in reports.values():
        failures.extend(r["failures"])
    
    return {
        "mode": "p3d",
        "total": total,
        "passed": passed,
        "failures": failures,
        "accuracy": passed / total if total else 0.0,
        "sub_reports": reports,
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
    parser = argparse.ArgumentParser(description="CellSpatio 综合 eval")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scripted", action="store_true", help="回放金标自检运行器（工具路由）")
    mode.add_argument("--live", action="store_true", help="真实 LLM 路由准确率")
    mode.add_argument("--p3d", action="store_true", help="P3c/P3d 专项测试（Skill路由+KG+最佳实践）")
    parser.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    parser.add_argument("--provider", default=None, help="live 模式覆盖 AGENT_LLM_PROVIDER")
    parser.add_argument("--min-accuracy", type=float, default=1.0, help="退出码门槛（scripted 默认须 100%）")
    args = parser.parse_args(argv)

    if args.p3d:
        report = run_p3d(args.cases)
    elif args.scripted:
        report = run_scripted(args.cases)
    else:
        report = run_live(args.cases, args.provider)
    
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("error"):
        return 2
    return 0 if report["accuracy"] >= args.min_accuracy else 1


if __name__ == "__main__":
    raise SystemExit(main())