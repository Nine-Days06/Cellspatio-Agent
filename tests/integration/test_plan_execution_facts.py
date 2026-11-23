"""方案读取与实际分析执行事实隔离的集成测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.control.workflow_manager import WorkflowManager
from src.control.workflow_recorder import WorkflowRecorder
from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeExec, FakeGen, FakeIntent, RecordingKGMemory


def test_completed_analysis_publishes_real_execution_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有 WorkflowManager 完成真实执行后，Recorder 才向 KG 发布产物。"""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(
        "src.control.snapshot_manager.SnapshotManager.create_snapshot",
        lambda self, run_id: tmp_path / "snapshot",
    )
    counts = tmp_path / "counts.csv"
    counts.write_text("gene,s1,s2\nG1,1,2\n", encoding="utf-8")
    kg = RecordingKGMemory()
    recorder = WorkflowRecorder(
        wrroc_base_dir=str(tmp_path / "wrroc"),
        repo_root=tmp_path,
        kg_memory=kg,
    )
    store = SessionStore(tmp_path / "sessions.db")
    manager = WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(),
        visualizer=SimpleNamespace(),
        r_script_generator=FakeGen(),
        workflow_recorder=recorder,
        analysis_roots=AnalysisRoots(store, tmp_path),
    )

    result = manager.run_analysis_for_agent(
        "differential_expression",
        {"input_files": [str(counts)], "question": "差异表达"},
        {"last_user_input": "做差异表达分析"},
    )

    assert result["status"] == "success"
    assert len(kg.records) == 1
    execution = kg.records[0]
    assert execution.steps
    assert any(step.tool == "run_analysis" for step in execution.steps)
    assert execution.outputs
    recorded_paths = {Path(output.path).resolve() for output in execution.outputs}
    output_path = Path(result["results"]["output_file"]).resolve()
    assert output_path in recorded_paths
    assert output_path.is_relative_to(tmp_path)
    assert output_path.is_file()
    assert output_path.read_text(encoding="utf-8").strip()
    capsule_path = Path(result["capsule_dir"]).resolve()
    assert capsule_path in recorded_paths
    assert capsule_path.is_relative_to(tmp_path)
    assert not (cwd / ".cellspatio").exists()
