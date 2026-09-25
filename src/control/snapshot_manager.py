"""Git Worktree 快照管理：创建/删除/列举/切换 snapshots/<run_id> 分支。"""
from __future__ import annotations

import subprocess
from pathlib import Path


class SnapshotManager:
    """管理 Git worktree 快照：每次分析创建 snapshots/<run_id> worktree。"""

    def __init__(self, repo_root: Path | str):
        self.repo_root = Path(repo_root).resolve()
        self.snapshots_dir = self.repo_root / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self, run_id: str, commit_msg: str | None = None) -> Path:
        """创建 worktree：snapshots/<run_id>，基于当前 HEAD"""
        snap_path = self.snapshots_dir / run_id
        if snap_path.exists():
            # 已存在：更新到最新 HEAD
            subprocess.run(["git", "fetch", "origin"], cwd=snap_path, check=False, capture_output=True)
            subprocess.run(["git", "reset", "--hard", "origin/HEAD"], cwd=snap_path, check=False, capture_output=True)
        else:
            # 新建 worktree
            branch_name = f"snapshot/{run_id}"
            subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(snap_path)],
                cwd=self.repo_root, check=True, capture_output=True
            )
        # 可选：提交当前变更作为快照记录（仅当有暂存变更时）
        if commit_msg:
            # 检查是否有暂存变更
            result = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=snap_path, capture_output=True, text=True, check=False)
            if result.stdout.strip():
                subprocess.run(["git", "add", "-A"], cwd=snap_path, check=True, capture_output=True)
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=snap_path, check=True, capture_output=True)
        return snap_path

    def delete_snapshot(self, run_id: str, force: bool = False) -> bool:
        """删除 worktree 与分支"""
        snap_path = self.snapshots_dir / run_id
        if not snap_path.exists():
            return False
        branch_name = f"snapshot/{run_id}"
        subprocess.run(["git", "worktree", "remove", "--force" if force else "", str(snap_path)],
                       cwd=self.repo_root, check=False, capture_output=True)
        subprocess.run(["git", "branch", "-D", branch_name], cwd=self.repo_root, check=False, capture_output=True)
        return True

    def list_snapshots(self) -> dict[str, str]:
        """返回 {run_id: path}"""
        result = {}
        for snap_dir in self.snapshots_dir.iterdir():
            if snap_dir.is_dir() and (snap_dir / ".git").exists():
                result[snap_dir.name] = str(snap_dir)
        return result

    def get_snapshot_path(self, run_id: str) -> Path | None:
        snap_path = self.snapshots_dir / run_id
        return snap_path if snap_path.exists() else None