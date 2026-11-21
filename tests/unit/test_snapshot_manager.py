"""SnapshotManager：Git worktree 创建/删除/列举/切换。"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=snap_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Add output" in result.stdout


def test_snapshot_manager_does_not_reset_existing_snapshot():
    """同名快照已存在时不得 reset 覆盖已有产物。"""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        import subprocess

        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

        mgr = SnapshotManager(repo_root=repo)
        snap_path = mgr.create_snapshot("run-001")
        generated = snap_path / "generated.txt"
        generated.write_text("keep me")

        assert mgr.create_snapshot("run-001") == snap_path
        assert generated.read_text() == "keep me"


def test_snapshot_manager_rejects_path_traversal(tmp_path):
    """run_id 不能把快照路径带出 snapshots/。"""
    mgr = SnapshotManager(tmp_path / "repo")
    for run_id in ("../outside", "a/b", "a\\b", str(tmp_path / "abs")):
        with pytest.raises(ValueError):
            mgr.get_snapshot_path(run_id)
        with pytest.raises(ValueError):
            mgr.create_snapshot(run_id)
        with pytest.raises(ValueError):
            mgr.delete_snapshot(run_id)


def test_create_snapshot_rejects_non_directory(tmp_path):
    """同名路径是普通文件时不能用它冒充快照。"""
    mgr = SnapshotManager(tmp_path / "repo")
    (mgr.snapshots_dir / "run-001").write_text("not a snapshot", encoding="utf-8")
    with pytest.raises(ValueError):
        mgr.create_snapshot("run-001")


def test_create_snapshot_rejects_symlink(tmp_path):
    """同名路径是符号链接时不得沿用它写产物。"""
    mgr = SnapshotManager(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = mgr.snapshots_dir / "run-001"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")
    with pytest.raises(ValueError):
        mgr.create_snapshot("run-001")


def test_delete_snapshot_omits_empty_force_argument(tmp_path, monkeypatch):
    """非强制删除不能把空字符串作为 git 参数传入。"""
    mgr = SnapshotManager(tmp_path / "repo")
    snap = mgr.snapshots_dir / "run-001"
    snap.mkdir()
    calls = []
    monkeypatch.setattr(
        "src.control.snapshot_manager.subprocess.run",
        lambda args, **kwargs: calls.append(args) or SimpleNamespace(stdout="", returncode=0),
    )

    assert mgr.delete_snapshot("run-001")
    assert calls[0] == ["git", "worktree", "remove", str(snap)]
    assert calls[1] == ["git", "branch", "-D", "snapshot/run-001"]

    calls.clear()
    assert mgr.delete_snapshot("run-001", force=True)
    assert calls[0] == ["git", "worktree", "remove", "--force", str(snap)]


def test_delete_snapshot_does_not_report_success_when_git_fails(tmp_path, monkeypatch):
    """worktree 删除失败时不得继续删分支或声称成功。"""
    mgr = SnapshotManager(tmp_path / "repo")
    (mgr.snapshots_dir / "run-001").mkdir()
    calls = []
    monkeypatch.setattr(
        "src.control.snapshot_manager.subprocess.run",
        lambda args, **kwargs: calls.append(args) or SimpleNamespace(stdout="", returncode=1),
    )

    assert not mgr.delete_snapshot("run-001")
    assert len(calls) == 1
