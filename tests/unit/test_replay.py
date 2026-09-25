"""Replay 复现接口测试。"""
import tempfile
from pathlib import Path

from src.control.replay import replay_run
from src.control.snapshot_manager import SnapshotManager


def test_replay_restores_and_runs():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "src" / "analysis").mkdir(parents=True)
        (repo / "data").mkdir()
        (repo / "data" / "input.csv").write_text("a,b\n1,2\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

        # 创建快照
        mgr = SnapshotManager(repo_root=repo)
        snap_path = mgr.create_snapshot("run-replay", commit_msg="Snapshot for replay")

        # 模拟 WRROC 记录（在 worktree 中写入结果）
        (snap_path / "out.de_results.csv").write_text("gene,padj\nG1,0.01\n")
        subprocess.run(["git", "add", "."], cwd=snap_path, check=True)
        subprocess.run(["git", "commit", "-m", "Analysis result"], cwd=snap_path, check=True)

        # Replay：应恢复到 worktree 并可读取结果
        result = replay_run("run-replay", repo_root=repo)
        assert result["status"] == "success"
        assert result["snapshot_path"] == str(mgr.get_snapshot_path("run-replay"))
        assert (Path(result["snapshot_path"]) / "out.de_results.csv").exists()