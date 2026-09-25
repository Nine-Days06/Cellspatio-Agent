"""WRROCStore 落盘、索引、查询、Lineage 测试。"""
import tempfile
from pathlib import Path

from src.control.wrroc_store import WRROCStore
from src.schemas.workflow import (
    StepOutput,
    StepType,
    TerminalStatus,
    WorkflowInput,
    WorkflowIntent,
    WorkflowOutput,
    WorkflowRun,
    WorkflowStep,
)


def _make_run(run_id: str) -> WorkflowRun:
    from src.schemas.workflow import WorkflowRun
    return WorkflowRun(
        run_id=run_id,
        intent=WorkflowIntent(type="analysis", analysis_type="differential_expression", original_input="test"),
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


def test_wrroc_persist_and_load():
    with tempfile.TemporaryDirectory() as tmp:
        store = WRROCStore(base_dir=Path(tmp) / ".wrroc")
        run = _make_run("run-001")
        store.persist(run)
        # 重新加载
        run2 = store.load("run-001")
        assert run2.run_id == "run-001"
        assert run2.intent.analysis_type.value == "differential_expression"
        assert len(run2.steps) == 1
        assert run2.outputs[0].path == "out.de_results.csv"


def test_wrroc_list_runs():
    with tempfile.TemporaryDirectory() as tmp:
        store = WRROCStore(base_dir=Path(tmp) / ".wrroc")
        store.persist(_make_run("run-001"))
        store.persist(_make_run("run-002"))
        runs = store.list_runs()
        assert len(runs) == 2
        ids = {r.run_id for r in runs}
        assert ids == {"run-001", "run-002"}


def test_wrroc_lineage_graph():
    """构建 Lineage：输入文件 → 步骤 → 输出文件 → 下游步骤"""
    with tempfile.TemporaryDirectory() as tmp:
        store = WRROCStore(base_dir=Path(tmp) / ".wrroc")
        # Run 1: 下载数据
        from src.schemas.workflow import (
            StepOutput,
            StepType,
            TerminalStatus,
            WorkflowInput,
            WorkflowIntent,
            WorkflowOutput,
            WorkflowRun,
            WorkflowStep,
        )
        run1 = WorkflowRun(
            run_id="run-download",
            intent=WorkflowIntent(type="fetch_data", original_input="下载 GSE123"),
            params={"query": "GSE123"},
            input=WorkflowInput(user_input="下载 GSE123", context={"downloaded_assets": [{"asset_id": "GSE123", "access_path": "data/GSE123.csv"}]}),
            steps=[WorkflowStep(step_id="s1", step_type=StepType.FETCH_DATA, tool="search_datasets", params={}, status="success",
                                output=StepOutput(status=TerminalStatus.SUCCESS, result={"candidates": [{"asset_id": "GSE123"}]}) )],
            outputs=[WorkflowOutput(name="GSE123", path="data/GSE123.csv", type="csv", meta={"source": "geo"})],
        )
        # Run 2: 分析（依赖 run1 的输出）
        run2 = WorkflowRun(
            run_id="run-analyze",
            intent=WorkflowIntent(type="analysis", analysis_type="differential_expression", original_input="分析差异表达"),
            params={"input_file": "data/GSE123.csv"},
            input=WorkflowInput(user_input="分析差异表达", context={"downloaded_assets": [{"asset_id": "GSE123", "access_path": "data/GSE123.csv"}]}),
            steps=[WorkflowStep(step_id="s1", step_type=StepType.ANALYSIS, tool="run_analysis", params={"analysis_type": "differential_expression"},
                                status="success", output=StepOutput(status=TerminalStatus.SUCCESS, result={"result_file": "out.de_results.csv"}))],
            outputs=[WorkflowOutput(name="de_results", path="out.de_results.csv", type="csv")],
        )
        store = WRROCStore(base_dir=Path(tmp) / ".wrroc")
        store.persist(run1)
        store.persist(run2)
        graph = store.build_lineage("run-analyze")
        # 正向追踪：run-analyze 依赖 run-download 的输出
        assert graph.has_edge("file:data/GSE123.csv", "run:run-analyze")
        # 验证图包含 run-analyze 节点和输入文件节点
        assert "run:run-analyze" in graph.nodes
        assert "file:data/GSE123.csv" in graph.nodes
        assert graph.has_edge("file:data/GSE123.csv", "run:run-analyze")