"""临时工作区占用统计与到期清理。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.ui.session_store import SessionStore

MAX_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024
# 上传 QC、聊天和空间分析没有统一运行表；近期有请求的工作区保守跳过，避免删到长任务。
ACTIVE_GRACE = timedelta(hours=2)


def tree_usage_bytes(path: str | Path | None) -> int:
    """统计目录树字节数；不存在或不可读按 0。"""
    if not path:
        return 0
    root = Path(path)
    if not root.exists():
        return 0
    total = 0
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def workspace_usage_bytes(store: SessionStore, workspace_id: str) -> int:
    """工作区当前文件占用。"""
    workspace = store.get_workspace(workspace_id)
    return tree_usage_bytes(workspace.get("root_path") if workspace else None)


def _delete_workspace_files(workspace: dict[str, Any], repo_root: Path) -> None:
    """只删除受管工作区根，绝不清理 legacy 或任意用户目录。"""
    root = workspace.get("root_path")
    if not root:
        return
    path = Path(root).resolve()
    managed = (repo_root / "data" / "workspaces").resolve()
    if not path.is_relative_to(managed) or path == managed:
        raise ValueError(f"拒绝删除非受管工作区目录：{path}")
    if path.exists():
        shutil.rmtree(path)


def _purge_wrroc_runs(repo_root: Path, session_ids: set[str]) -> None:
    """删除 .wrroc 下属于即将销毁会话的运行记录；不碰共享 KG 记忆。"""
    base = repo_root / ".wrroc"
    if not base.is_dir():
        return
    for run_dir in base.iterdir():
        workflow = run_dir / "workflow.json"
        if not run_dir.is_dir() or not workflow.is_file():
            continue
        try:
            payload = json.loads(workflow.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        context = (payload.get("input") or {}).get("context") or {}
        if context.get("session_id") in session_ids:
            shutil.rmtree(run_dir, ignore_errors=True)


def cleanup_expired_workspaces(
    store: SessionStore,
    repo_root: str | Path,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """删除到期临时工作区；仍有受控阶段运行的工作区跳过。"""
    now = now or datetime.now(timezone.utc)
    repo_root = Path(repo_root).resolve()
    expired: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for workspace in store.list_workspaces(include_inactive=True):
        if workspace["kind"] != "ephemeral" or workspace["status"] != "active":
            continue
        expires_at = workspace.get("expires_at")
        if not expires_at:
            continue
        try:
            parsed = datetime.fromisoformat(expires_at)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed > now:
            continue
        scoped = SessionStore(db_path=store.db_path, workspace_id=workspace["id"])
        sessions = scoped.list_sessions(include_archived=True)
        running = [s["id"] for s in sessions if scoped.running_stage(s["id"])]
        if running:
            skipped.append({"workspace_id": workspace["id"], "reason": "分析运行中"})
            continue
        last_seen = workspace.get("last_seen_at")
        if last_seen:
            try:
                seen_at = datetime.fromisoformat(last_seen)
            except ValueError:
                seen_at = None
            if seen_at is not None:
                if seen_at.tzinfo is None:
                    seen_at = seen_at.replace(tzinfo=timezone.utc)
                if seen_at < parsed and now - seen_at < ACTIVE_GRACE:
                    skipped.append({"workspace_id": workspace["id"], "reason": "近期有活动"})
                    continue
        expired.append(workspace)

    if not dry_run:
        for workspace in expired:
            scoped = SessionStore(db_path=store.db_path, workspace_id=workspace["id"])
            session_ids = {row["id"] for row in scoped.list_sessions(include_archived=True)}
            _delete_workspace_files(workspace, repo_root)
            _purge_wrroc_runs(repo_root, session_ids)
            store.delete_workspace(workspace["id"])
    return {
        "status": "dry-run" if dry_run else "ok",
        "expired": [workspace["id"] for workspace in expired],
        "skipped": skipped,
        "count": len(expired),
    }
