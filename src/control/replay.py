"""复现历史运行：恢复快照并尝试同步环境，不重跑分析。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from src.control.snapshot_manager import SnapshotManager


def replay_run(run_id: str, repo_root: Path | str | None = None) -> dict[str, Any]:
    """
    复现历史运行：
    1. 定位 snapshots/<run_id> worktree
    2. 可选：同步环境（uv sync / renv::restore）
    3. 返回快照路径与状态

    只恢复快照、尝试环境同步，不重跑分析；成功状态为 ``restored``。
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
        subprocess.run(
            ["R", "-e", "renv::restore()"], cwd=snap_path, check=False, capture_output=True
        )

    return {
        "status": "restored",
        "run_id": run_id,
        "snapshot_path": str(snap_path),
        "message": f"Snapshot restored for {run_id}; analysis not re-executed",
    }
