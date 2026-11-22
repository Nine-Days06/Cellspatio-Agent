"""会话/项目记录删除：先原子移入 .trash，DB 删除成功后才丢弃产物。

CLI 与 HTTP 删除端点共用本实现，避免两套删除范围漂移。
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ui.record_files import resolve_dir, run_dirs, safe_target, session_runs, wrroc_runs
from src.ui.session_store import SessionStore


def _delete_plan(
    scope: str, target_id: str, store: SessionStore, root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[Path], list[str]]:
    """列出待删会话、项目、产物路径与越界跳过项。"""
    project = None
    if scope == "session":
        session = store.get_session(target_id)
        if session is None:
            raise ValueError("会话不存在")
        sessions = [session]
    elif scope == "project":
        project = store.get_project(target_id)
        if project is None:
            raise ValueError("项目不存在")
        sessions = [
            session
            for session in store.list_sessions(include_archived=True)
            if session["project_id"] == target_id
        ]
    else:
        raise ValueError("scope 必须是 session 或 project")

    paths: list[Path] = []
    skipped: list[str] = []
    for session in sessions:
        running = store.running_stage(session["id"])
        if running:
            raise ValueError(f"{running}运行中，不能删除会话")
        for run in session_runs(store, session["id"]):
            for _, directory in run_dirs(run):
                path = safe_target(directory, root)
                if path is None:
                    skipped.append(str(resolve_dir(directory, root)))
                else:
                    paths.append(path)
            for name in (".wrroc", "snapshots"):
                candidate = safe_target(root / name / str(run["run_id"]), root)
                if candidate is None:
                    skipped.append(str(root / name / str(run["run_id"])))
                else:
                    paths.append(candidate)
        for run_id, _ in wrroc_runs(root, session["id"]):
            for name in (".wrroc", "snapshots"):
                candidate = safe_target(root / name / run_id, root)
                if candidate is None:
                    skipped.append(str(root / name / run_id))
                else:
                    paths.append(candidate)
        # 分析输出（DE/空间 CSV、capsule 目录）登记在 workflow.json 的 outputs 里。
        for _, data in wrroc_runs(root, session["id"]):
            for output in data.get("outputs") or []:
                raw = output.get("path") if isinstance(output, Mapping) else None
                path = safe_target(raw, root) if raw else None
                # 数据根之外的产物属于用户文件，不纳入删除，也不阻断删除。
                if path is not None:
                    paths.append(path)
    if project is not None:
        for candidate in (
            root / "data" / "projects" / target_id,
            root / ".wrroc" / "kg_memory" / target_id,
        ):
            path = safe_target(candidate, root)
            if path is None:
                skipped.append(str(candidate))
            else:
                paths.append(path)
    unique_paths = []
    seen: set[Path] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique_paths.append(path)
    return sessions, project, _outermost(unique_paths), skipped


def _outermost(paths: list[Path]) -> list[Path]:
    """去掉被其他待删目录包含的子路径，避免重复移动。"""
    result: list[Path] = []
    for path in sorted(paths, key=lambda item: len(item.parts)):
        if all(not path.is_relative_to(parent) for parent in result):
            result.append(path)
    return result


def _restore_paths(moves: list[tuple[Path, Path]]) -> list[str]:
    """把暂存路径移回原位；返回未能回滚的路径。"""
    failed = []
    for original, staged in reversed(moves):
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            staged.rename(original)
        except OSError:
            failed.append(str(staged))
    return failed


def _stage_paths(paths: list[Path], root: Path) -> tuple[Path, list[tuple[Path, Path]]]:
    """把待删路径原子移入 .trash，DB 删除成功后才真正丢弃。"""
    trash = root / ".trash" / f"delete-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    moves: list[tuple[Path, Path]] = []
    try:
        for path in paths:
            if not path.exists():
                continue
            target = trash / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
            moves.append((path, target))
    except OSError:
        _restore_paths(moves)
        raise
    return trash, moves


def delete_records(
    scope: str,
    target_id: str,
    *,
    dry_run: bool = False,
    cascade: bool = False,
    store: SessionStore | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """删除 DB 行与已知产物目录；dry_run 只列计划，不落盘。"""
    base = Path(root or Path.cwd()).resolve()
    if store is None:
        store = (
            SessionStore() if root is None else SessionStore(base / ".cellspatio" / "sessions.db")
        )
    sessions, project, paths, skipped = _delete_plan(scope, target_id, store, base)
    plan = {
        "scope": scope,
        "target_id": target_id,
        "sessions": [session["id"] for session in sessions],
        "paths": [str(path) for path in paths],
        "skipped": skipped,
        "cascade": cascade,
    }
    if dry_run:
        return {
            "status": "dry-run",
            **plan,
            "requires_cascade": bool(scope == "project" and sessions and not cascade),
        }

    if scope == "project" and sessions and not cascade:
        raise ValueError("项目仍有会话，需显式 --cascade")
    if skipped:
        raise ValueError("有产物在数据根之外，未删除任何记录: " + "; ".join(skipped))

    try:
        trash, moves = _stage_paths(paths, base) if paths else (None, [])
    except OSError as exc:
        return {
            "status": "files-failed",
            **plan,
            "file_failures": [{"path": str(exc.filename or ""), "error": str(exc)}],
            "verified": False,
        }

    try:
        # 单事务：中途失败不能留下“部分会话已删、文件已搬走”的半删状态。
        store.delete_sessions_and_project(
            [session["id"] for session in sessions],
            target_id if project is not None else None,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {
            "status": "db-failed",
            "error": str(exc),
            **plan,
            "restore_failures": _restore_paths(moves),
            "verified": False,
        }

    if trash is not None:
        shutil.rmtree(trash, ignore_errors=True)

    remaining_sessions = [
        session["id"] for session in sessions if store.get_session(session["id"]) is not None
    ]
    remaining_paths = [str(path) for path in paths if path.exists()]
    verified = not remaining_sessions and not remaining_paths
    return {
        "status": "ok" if verified else "verify-failed",
        **plan,
        "deleted_sessions": len(sessions),
        "deleted_paths": len(paths) - len(remaining_paths),
        "remaining_sessions": remaining_sessions,
        "remaining_paths": remaining_paths,
        "verified": verified,
    }
