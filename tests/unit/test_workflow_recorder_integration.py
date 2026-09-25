"""WorkflowRecorder 与 WorkflowManager 集成测试。"""
import tempfile
from pathlib import Path

from src.control.workflow_recorder import WorkflowRecorder
from src.main import CellSpatioAgent


def _get_run_id(recorder: WorkflowRecorder) -> str:
    """从 recorder 上下文获取最新的 run_id。"""
    ctx = recorder.get_context()
    if not ctx:
        raise ValueError("No run_id found in recorder context")
    return list(ctx.keys())[-1]


def _init_git_repo(path: Path):
    """在指定路径初始化 git 仓库。"""
    import subprocess
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True)


def test_workflow_manager_with_recorder(monkeypatch):
    """WorkflowManager 注入 Recorder 后自动记录执行轨迹。"""
    monkeypatch.setattr("src.main.get_current_llm", lambda: (None, "test-model"))
    monkeypatch.setattr("src.main.LightRAGClient.__init__", lambda self, *a, **k: None)

    with tempfile.TemporaryDirectory() as tmp:
        # 初始化 git 仓库以支持 snapshot 创建
        _init_git_repo(Path(tmp))

        csv_file = Path(tmp) / "counts.csv"
        csv_file.write_text("gene,sample1,sample2\nTP53,10,12\nGAPDH,100,110\n", encoding="utf-8")

        config = {"knowledge_dir": tmp, "data_dir": tmp, "repo_root": tmp}
        agent = CellSpatioAgent(config)

        # 验证 recorder 注入
        assert hasattr(agent.workflow_manager, "workflow_recorder")
        assert isinstance(agent.workflow_manager.workflow_recorder, WorkflowRecorder)

        context = {"downloaded_assets": [{"source": "geo", "asset_id": "GSE1", "access_path": str(csv_file)}]}
        result = agent.execute_workflow("分析差异表达基因", context=context)

        # 验证 HITL 门
        assert result["status"] in ("needs_script_confirmation", "success")
        assert result["analysis_type"] == "differential_expression"

        # 验证 recorder 捕获了执行轨迹
        recorder = agent.workflow_manager.workflow_recorder
        run_id = _get_run_id(recorder)
        jsonl = recorder.export_jsonl(run_id)
        assert recorder is not None
        assert len(jsonl.strip().split("\n")) >= 1


def test_workflow_manager_recorder_captures_intent_and_steps(monkeypatch):
    """验证 recorder 捕获 intent 解析、参数提取、步骤执行。"""
    monkeypatch.setattr("src.main.get_current_llm", lambda: (None, "test-model"))
    monkeypatch.setattr("src.main.LightRAGClient.__init__", lambda self, *a, **k: None)

    with tempfile.TemporaryDirectory() as tmp:
        # 初始化 git 仓库以支持 snapshot 创建
        _init_git_repo(Path(tmp))

        csv_file = Path(tmp) / "counts.csv"
        csv_file.write_text("gene,sample1,sample2\nTP53,10,12\nGAPDH,100,110\n", encoding="utf-8")

        config = {"knowledge_dir": tmp, "data_dir": tmp, "repo_root": tmp}
        agent = CellSpatioAgent(config)

        context = {"downloaded_assets": [{"source": "geo", "asset_id": "GSE1", "access_path": str(csv_file)}]}
        _ = agent.execute_workflow("分析差异表达基因", context=context)

        recorder = agent.workflow_manager.workflow_recorder
        run_id = _get_run_id(recorder)
        record = recorder.get_record(run_id)

        # 验证 intent 记录
        assert record.intent.type.value == "analysis"
        assert record.intent.analysis_type.value == "differential_expression"
        assert "差异表达" in record.intent.original_input

        # 验证参数记录（IntentParser 从文本提取，context 中的文件路径不在 parameters 中）
        # input_files 为空是预期行为，因为用户文本中未包含文件路径
        assert record.parameters.input_files == []

        # 验证步骤记录
        assert len(record.steps) >= 1
        step = record.steps[0]
        assert step.tool == "run_analysis"
        assert step.step_type.value == "analysis"


def test_workflow_manager_without_recorder_still_works(monkeypatch):
    """未注入 recorder 时 WorkflowManager 正常工作（向后兼容）。"""
    from src.analysis.r_executor import RExecutor
    from src.analysis.visualization import Visualizer
    from src.control.intent_parser import IntentParser
    from src.control.r_script_generator import RScriptGenerator
    from src.control.workflow_manager import WorkflowManager

    monkeypatch.setattr("src.main.get_current_llm", lambda: (None, "test-model"))
    monkeypatch.setattr("src.main.LightRAGClient.__init__", lambda self, *a, **k: None)

    manager = WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=None,
        r_executor=RExecutor(),
        visualizer=Visualizer(),
        r_script_generator=RScriptGenerator(),
        require_script_confirmation=False,
    )

    result = manager.execute_workflow("分析差异表达基因")
    assert result["status"] == "needs_input"  # 无数据时返回 needs_input
    assert result["analysis_type"] == "differential_expression"