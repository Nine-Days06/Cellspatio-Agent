"""会话绑定的本地 QC 预览与批准；不接入旧脚本执行接口。"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.analysis.qc_preview import Thresholds, build_preview, check_preview, checked_evidence
from src.analysis.sc_filter import FILTER_TOOL_VERSION, run_sc_filter, verified_selection
from src.api.chat_service import session_lock
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/sessions/{session_id}/qc", tags=["qc"])


def qc_root(agent: Any) -> Path:
    """与 WorkflowManager 的固定 QC 根目录一致，路径不由模型指定。"""
    recorder = getattr(getattr(agent, "workflow_manager", None), "workflow_recorder", None)
    root = Path(recorder.repo_root) if recorder else Path.cwd()
    return (root / "data" / "analysis" / "sc_qc").resolve()


def require_run(store: SessionStore, session_id: str, run_id: str) -> dict[str, Any]:
    """关联缺失/跨会话统一 404，不扫描旧消息路径。"""
    run = store.get_qc_run(session_id, run_id)
    if run is None:
        raise HTTPException(404, "QC 关联不存在；旧证据需重新 QC 才能交互")
    return run


def view(run: dict[str, Any], blocked: str | None = None) -> dict[str, Any]:
    """只返回界面需要的权威状态，不返回原输入或逐细胞内容。"""
    return {
        "run_id": run["run_id"],
        "revision": run["revision"],
        "qc": run["evidence"]["summary"],
        "active_preview": next(
            (p for p in run["previews"] if p["preview_id"] == run["active_preview_id"]), None
        ),
        "approvals": [p for p in run["previews"] if p["status"] == "approved"],
        "blocked": [blocked] if blocked else [],
        "attempts": [
            {
                **{
                    key: attempt[key]
                    for key in (
                        "attempt_id",
                        "preview_id",
                        "status",
                        "directory",
                        "created_at",
                        "finished_at",
                    )
                },
                "result": {
                    key: value
                    for key, value in attempt["result"].items()
                    if key
                    in ("cells", "features", "error", "versions", "artifacts", "scientific_status")
                }
                if attempt["result"]
                else None,
            }
            for attempt in run.get("attempts", [])
        ],
    }


@router.get("/{run_id}")
def get_qc(session_id: str, run_id: str, request: Request) -> dict[str, Any]:
    """刷新后仍显示精确批准历史；变化证据显示阻断。"""
    run = require_run(SessionStore(), session_id, run_id)
    try:
        checked_evidence(run, qc_root(request.app.state.agent))
        active = next(
            (p for p in run["previews"] if p["preview_id"] == run["active_preview_id"]), None
        )
        if active and active["status"] == "pending":
            check_preview(run, qc_root(request.app.state.agent), active)
    except (OSError, ValueError) as exc:
        return view(run, str(exc))
    return view(run)


@router.post("/{run_id}/preview")
def preview_qc(session_id: str, run_id: str, body: Thresholds, request: Request) -> dict[str, Any]:
    """生成独立预览，以数据库版本条件替换 active 预览。"""
    store = SessionStore()
    run = require_run(store, session_id, run_id)
    try:
        preview = build_preview(run, qc_root(request.app.state.agent), body)
        store.activate_qc_preview(session_id, run_id, run["revision"], preview)
    except (OSError, ValueError, sqlite3.OperationalError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return view(require_run(store, session_id, run_id))


class ApprovalRequest(BaseModel):
    """只有预览身份与本地选择依据，没有脚本或执行授权。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    preview_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/{run_id}/approve")
def approve_qc(
    session_id: str, run_id: str, body: ApprovalRequest, request: Request
) -> dict[str, Any]:
    """验证证据后短事务批准；已批准请求幂等返回历史，不再次计算。"""
    store = SessionStore()
    run = require_run(store, session_id, run_id)
    preview = next((p for p in run["previews"] if p["preview_id"] == body.preview_id), None)
    if preview is None:
        raise HTTPException(409, "预览不存在，请重新预览")
    try:
        if preview["status"] != "approved":
            check_preview(run, qc_root(request.app.state.agent), preview)
        approved = store.approve_qc_preview(
            session_id, run_id, run["revision"], body.preview_id, body.reason
        )
    except (OSError, ValueError, sqlite3.OperationalError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return view(approved)


class FilterRequest(BaseModel):
    """独立执行授权只携带预览身份，不接受路径或脚本。"""

    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=64)


@router.post("/{run_id}/filter")
def filter_qc(
    session_id: str, run_id: str, body: FilterRequest, request: Request
) -> dict[str, Any]:
    """固定过滤执行；重复授权只返回历史，不调用 R。"""
    store = SessionStore()
    run = require_run(store, session_id, run_id)
    previous = next(
        (
            a
            for a in run.get("attempts", [])
            if a["preview_id"] == body.preview_id and a["tool_version"] == FILTER_TOOL_VERSION
        ),
        None,
    )
    if previous:
        return view(run)
    lock = session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "该会话已有任务在进行")
    try:
        run = require_run(store, session_id, run_id)
        preview = next((p for p in run["previews"] if p["preview_id"] == body.preview_id), None)
        if (
            preview is None
            or preview["status"] != "approved"
            or run["active_preview_id"] != body.preview_id
        ):
            raise ValueError("仅可执行当前已批准选择")
        root = qc_root(request.app.state.agent)
        verified_selection(run, root, preview)
        attempt_id = uuid.uuid4().hex
        directory = root.parent / "sc_filter" / attempt_id
        if directory.parent.resolve() != root.parent / "sc_filter" or directory.exists():
            raise ValueError("过滤输出目录非法")
        attempt = store.start_sc_filter(
            session_id,
            run_id,
            run["revision"],
            body.preview_id,
            preview["binding_sha256"],
            FILTER_TOOL_VERSION,
            str(directory),
            attempt_id,
        )
        if attempt["claimed"]:
            try:
                result = run_sc_filter(
                    run,
                    preview,
                    root,
                    directory,
                    request.app.state.agent.workflow_manager.r_executor,
                )
            except OSError as exc:
                result = {
                    "status": "failed",
                    "stage": "filter_review",
                    "scientific_status": "unreviewed",
                    "error": str(exc),
                }
            store.finish_sc_filter(attempt_id, result)
        return view(require_run(store, session_id, run_id))
    except (OSError, ValueError, sqlite3.OperationalError) as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        lock.release()
