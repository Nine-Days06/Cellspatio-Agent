"""SnapshotManager：Git worktree 创建/删除/列举/切换。"""
import tempfile
from pathlib import Path

from src.control.snapshot_manager import SnapshotManager


def test_snapshot_manager_creates_worktree():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        # 初始化 git 仓库
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

        mgr = SnapshotManager(repo_root=repo)
        snap_path = mgr.create_snapshot("run-001", commit_msg="Snapshot for run-001")
        assert snap_path.exists()
        assert (snap_path / ".git").exists()  # worktree 有独立 .git
        assert (snap_path / "README.md").exists()

        # 列举
        snaps = mgr.list_snapshots()
        assert "run-001" in snaps
        assert snaps["run-001"] == str(snap_path)


def test_snapshot_manager_switches_context():
    """创建快照后可在 worktree 中运行命令"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        import subprocess
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "file.txt").write_text("v1")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

        mgr = SnapshotManager(repo_root=repo)
        snap_path = mgr.create_snapshot("run-001", commit_msg="Snap run-001")

        # 在 worktree 中写入新文件
        (snap_path / "generated.txt").write_text("output")
        subprocess.run(["git", "add", "."], cwd=snap_path, check=True)
        subprocess.run(["git", "commit", "-m", "Add output"], cwd=snap_path, check=True)

        # 验证 worktree 有新提交
        result = subprocess.run(["git", "log", "--oneline", "-1"], cwd=snap_path, capture_output=True, text=True, check=False)
        assert "Add output" in result.stdout