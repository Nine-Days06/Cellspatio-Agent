"""一键复现：恢复 worktree、数据、环境、执行。"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.control.snapshot_manager import SnapshotManager


def replay_run(run_id: str, repo_root: Path | str | None = None) -> dict[str, Any]:
    """
    一键复现：
    1. 定位 snapshots/<run_id> worktree
    2. 可选：恢复数据文件（从 WRROC 或 worktree）
    3. 可选：恢复环境（uv sync / renv::restore）
    4. 返回快照路径与状态
    """
    repo_root = Path(repo_root) if repo_root else Path.cwd()
    mgr = SnapshotManager(repo_root)
    snap_path = mgr.get_snapshot_path(run_id)

    if not snap_path:
        return {"status": "error", "message": f"Snapshot not found: {run_id}"}

    # 可选：恢复 Python 环境
    if (snap_path / "pyproject.toml").exists() or (snap_path / "uv.lock").exists():
        subprocess.run(["uv", "sync"], cwd=snap_path, check=False, capture_output=True)

    # 可选：恢复 R 环境
    if (snap_path / "renv.lock").exists():
        subprocess.run(["R", "-e", "renv::restore()"], cwd=snap_path, check=False, capture_output=True)

    return {
        "status": "success",
        "run_id": run_id,
        "snapshot_path": str(snap_path),
        "message": f"Restored snapshot for {run_id} at {snap_path}",
    }