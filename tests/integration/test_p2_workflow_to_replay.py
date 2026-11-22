"""Phase 2 真实本地执行：workflow → WRROC → Snapshot → Replay。"""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.analysis.r_executor import RExecutor
from src.control.intent_parser import IntentParser
from src.control.replay import replay_run
from src.control.snapshot_manager import SnapshotManager
from src.control.workflow_manager import WorkflowManager
from src.control.workflow_recorder import WorkflowRecorder
from src.control.wrroc_store import WRROCStore
from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore


class BaseRGenerator:
    """代替外部生成器，使用不依赖分析包的基础 R 落盘脚本。"""

    def generate_code(
        self, analysis_type: str, params: dict[str, Any], method_context: str | None = None
    ) -> str:
        """为真实执行器提供一个已知产物。"""
        output = Path(params["output_file"]).as_posix()
        return f'write.csv(data.frame(gene="G1", padj=0.01), "{output}", row.names=FALSE)\n'


def test_full_workflow_with_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """直接完成路径才生成真实 Git 快照，Replay 只恢复快照。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "0")
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "test: baseline"], cwd=repo, check=True)
    source = repo / "counts.csv"
    source.write_text("gene,A,B\nG1,10,12\n", encoding="utf-8")
    recorder = WorkflowRecorder(repo / ".wrroc", repo_root=repo)
    manager = WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=None,
        r_executor=RExecutor(),
        visualizer=None,
        r_script_generator=BaseRGenerator(),
        workflow_recorder=recorder,
        require_script_confirmation=False,
        analysis_roots=AnalysisRoots(SessionStore(tmp_path / "sessions.db"), repo),
    )

    result = manager.execute_workflow(
        "分析差异表达基因", context={"downloaded_assets": [{"access_path": str(source)}]}
    )

    assert result["status"] == "success"
    runs = WRROCStore(repo / ".wrroc").list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.intent.analysis_type == "differential_expression"
    assert run.steps[0].tool == "run_analysis"
    assert Path(result["results"]["output_file"]).is_file()
    assert any(output.path == result["results"]["output_file"] for output in run.outputs)
    snapshots = SnapshotManager(repo).list_snapshots()
    assert list(snapshots) == [run.run_id]
    assert replay_run(run.run_id, repo_root=repo)["status"] == "restored"
