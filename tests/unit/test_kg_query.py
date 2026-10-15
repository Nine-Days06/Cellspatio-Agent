"""KGQuery：自然语言查询接口测试。"""

import tempfile
from pathlib import Path

from src.control.kg_memory import KGMemory
from src.control.kg_query import KGQuery
from src.schemas.workflow import (
    AnalysisType,
    IntentType,
    StepOutput,
    StepType,
    TerminalStatus,
    WorkflowInput,
    WorkflowIntent,
    WorkflowOutput,
    WorkflowRun,
    WorkflowStep,
)


def _make_run() -> "WorkflowRun":
    return WorkflowRun(
        run_id="test-run-kgquery-001",
        intent=WorkflowIntent(
            type=IntentType.ANALYSIS,
            analysis_type=AnalysisType.DIFFERENTIAL_EXPRESSION,
            original_input="test",
        ),
        params={"input_file": "data.csv"},
        input=WorkflowInput(user_input="test"),
        steps=[
            WorkflowStep(
                step_id="s1",
                step_type=StepType.ANALYSIS,
                tool="run_analysis",
                params={"analysis_type": "differential_expression"},
                output=StepOutput(
                    status=TerminalStatus.SUCCESS, result={"result_file": "out.de_results.csv"}
                ),
            ),
        ],
        outputs=[
            WorkflowOutput(
                name="de_results", path="out.de_results.csv", type="csv", meta={"genes": 100}
            ),
        ],
    )


def test_kg_query_ingest_and_query():
    """测试 ingest 和 query 在同一个 KGQuery 实例上工作（PG 后端）。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 创建 KGMemory，传入 KGQuery 以复用共享存储；workspace 唯一避免跨测试污染
        kg_memory = KGMemory(
            working_dir=Path(tmp) / "kg", workspace=f"test_kgquery_{Path(tmp).name}"
        )
        try:
            kg_query = KGQuery(working_dir=Path(tmp) / "kg", kg_memory=kg_memory)
            run = _make_run()
            kg_query.ingest_workflow(run)
            # 自然语言查询
            answer = kg_query.query("What is test-run-kgquery-001?")
            assert isinstance(answer, str)
            assert len(answer) > 0
        finally:
            kg_memory.close()
