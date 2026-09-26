"""KGMemory：工作流记录增量写入 LightRAG 测试。"""
import tempfile
from pathlib import Path
import pytest
from src.control.kg_memory import KGMemory
from src.schemas.workflow import WorkflowRun, WorkflowIntent, WorkflowInput, WorkflowStep, WorkflowOutput, TerminalStatus, StepType, AnalysisType, IntentType, StepOutput


def _make_run() -> "WorkflowRun":
    return WorkflowRun(
        run_id="test-run-kgmem-001",
        intent=WorkflowIntent(type=IntentType.ANALYSIS, analysis_type=AnalysisType.DIFFERENTIAL_EXPRESSION, original_input="test"),
        params={"input_file": "data.csv"},
        input=WorkflowInput(user_input="test"),
        steps=[
            WorkflowStep(
                step_id="s1",
                step_type=StepType.ANALYSIS,
                tool="run_analysis",
                params={"analysis_type": "differential_expression"},
                output=StepOutput(status=TerminalStatus.SUCCESS, result={"result_file": "out.de_results.csv"}),
            ),
        ],
        outputs=[
            WorkflowOutput(name="de_results", path="out.de_results.csv", type="csv", meta={"genes": 100}),
        ],
    )


def test_kg_memory_ingest_and_query():
    """测试 ingest 和 query 在同一个 KGMemory 实例上工作。"""
    with tempfile.TemporaryDirectory() as tmp:
        kg_mem = KGMemory(working_dir=Path(tmp) / "kg")
        run = _make_run()
        kg_mem.ingest(run)
        # 验证 LightRAG 写入（文件已创建在 workspace 子目录中）
        kg_dir = Path(tmp) / "kg"
        # 找到 workspace 子目录
        workspace_dirs = list(kg_dir.glob("kg_*"))
        assert len(workspace_dirs) == 1
        workspace_dir = workspace_dirs[0]
        assert (workspace_dir / "vdb_entities.json").exists()
        # 查询实体 - mock LLM 可能不返回实体，但不应报错
        entities = kg_mem.query_entities("test-run-kgmem-001")
        assert isinstance(entities, list)