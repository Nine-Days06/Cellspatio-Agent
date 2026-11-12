"""会话/项目记录占用只读汇总（大小与文件数），不做清理。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.ui.record_files import run_dirs, safe_target, session_runs, wrroc_index
from src.ui.session_store import SessionStore


def _tree_usage(path: Path) -> tuple[int, int]:
    """统计目录内文件总字节数与文件数。"""
    total = files = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += item.stat().st_size
            files += 1
        except OSError:
            continue
    return total, files


def _add_existing(path: Path | None, seen: set[Path], paths: list[Path]) -> None:
    """去重后收集存在的路径。"""
    if path is None or path in seen or not path.exists():
        return
    seen.add(path)
    paths.append(path)


def _session_paths(
    store: SessionStore,
    session_id: str,
    root: Path,
    wrroc: dict[str, list[tuple[str, dict[str, Any]]]],
) -> list[Path]:
    """会话产物目录 + .wrroc/<run_id> + snapshots/<run_id>。"""
    paths: list[Path] = []
    seen: set[Path] = set()
    run_ids: set[str] = set()
    for run in session_runs(store, session_id):
        run_ids.add(str(run["run_id"]))
        for _, directory in run_dirs(run):
            _add_existing(safe_target(directory, root), seen, paths)
    for run_id, _ in wrroc.get(session_id, ()):
        run_ids.add(run_id)
    for run_id in run_ids:
        for name in (".wrroc", "snapshots"):
            _add_existing(safe_target(root / name / run_id, root), seen, paths)
    return paths


def _summarize(paths: list[Path]) -> dict[str, Any]:
    """汇总路径总字节数与文件数。"""
    total = files = 0
    for path in paths:
        try:
            if path.is_dir():
                size, count = _tree_usage(path)
            else:
                size, count = path.stat().st_size, 1
        except OSError:
            continue
        total += size
        files += count
    return {"bytes": total, "files": files}


def session_usage(store: SessionStore, session_id: str, root: Path) -> dict[str, Any]:
    """只读汇总一个会话的记录占用。"""
    if store.get_session(session_id) is None:
        raise ValueError("会话不存在")
    return {
        "scope": "session",
        "target_id": session_id,
        **_summarize(_session_paths(store, session_id, root, wrroc_index(root))),
    }


def project_usage(store: SessionStore, project_id: str, root: Path) -> dict[str, Any]:
    """只读汇总一个项目：项目目录 + 各会话产物 + kg_memory，目录不重复计数。"""
    if store.get_project(project_id) is None:
        raise ValueError("项目不存在")
    paths: list[Path] = []
    seen: set[Path] = set()
    wrroc = wrroc_index(root)
    project_dir = safe_target(root / "data" / "projects" / project_id, root)
    # 项目目录整树只算一次（含未登记的 de/spatial 产物）
    _add_existing(project_dir, seen, paths)
    _add_existing(safe_target(root / ".wrroc" / "kg_memory" / project_id, root), seen, paths)
    sessions = [
        session
        for session in store.list_sessions(include_archived=True)
        if session["project_id"] == project_id
    ]
    for session in sessions:
        for path in _session_paths(store, session["id"], root, wrroc):
            if project_dir is not None and path.is_relative_to(project_dir):
                continue
            _add_existing(path, seen, paths)
    return {
        "scope": "project",
        "target_id": project_id,
        **_summarize(paths),
    }
