"""会话绑定的本地 QC 预览与批准；不接入旧脚本执行接口。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.analysis.qc_preview import Thresholds, build_preview, check_preview, checked_evidence
from src.analysis.sc_filter import FILTER_TOOL_VERSION, run_sc_filter, verified_selection
from src.api.authorized_run import StageClaim, run_authorized
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/sessions/{session_id}/qc", tags=["qc"])


def run_qc_root(agent: Any, run: dict[str, Any]) -> Path:
    """校验 run 目录在旧全局根或项目分析根内，返回 QC 根。"""
    root = Path(run["directory"]).resolve().parent
    recorder = getattr(getattr(agent, "workflow_manager", None), "workflow_recorder", None)
    repo = Path(recorder.repo_root) if recorder else Path.cwd()
    legacy = (repo / "data" / "analysis" / "sc_qc").resolve()
    projects = (repo / "data" / "projects").resolve()
    project_root = (
        root.name == "sc_qc"
        and root.parent.name == "analysis"
        and root.parent.parent.parent == projects
    )
    if root != legacy and not project_root:
        raise ValueError("QC 目录非法，请重新 QC")
    return root


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
        "pca_running": any(a["status"] == "running" for a in run.get("pca_attempts", [])),
        "cluster_running": any(a["status"] == "running" for a in run.get("cluster_attempts", [])),
        "marker_running": any(a["status"] == "running" for a in run.get("marker_attempts", [])),
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
        checked_evidence(run, run_qc_root(request.app.state.agent, run))
        active = next(
            (p for p in run["previews"] if p["preview_id"] == run["active_preview_id"]), None
        )
        if active and active["status"] == "pending":
            check_preview(run, run_qc_root(request.app.state.agent, run), active)
    except (OSError, ValueError) as exc:
        return view(run, str(exc))
    return view(run)


@router.post("/{run_id}/preview")
def preview_qc(session_id: str, run_id: str, body: Thresholds, request: Request) -> dict[str, Any]:
    """生成独立预览，以数据库版本条件替换 active 预览。"""
    store = SessionStore()
    run = require_run(store, session_id, run_id)
    try:
        preview = build_preview(run, run_qc_root(request.app.state.agent, run), body)
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
            check_preview(run, run_qc_root(request.app.state.agent, run), preview)
        approved = store.approve_qc_preview(
            session_id, run_id, run["revision"], body.preview_id, body.reason
        )
    except (OSError, ValueError, sqlite3.OperationalError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return view(approved)


class RejectionRequest(BaseModel):
    """拒绝只携带当前预览身份与本地理由，不执行过滤。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    preview_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/{run_id}/reject")
def reject_qc(session_id: str, run_id: str, body: RejectionRequest) -> dict[str, Any]:
    """记录当前待批准预览的拒绝；已批准预览与已执行 run 不可拒绝。"""
    store = SessionStore()
    require_run(store, session_id, run_id)
    try:
        run = store.record_qc_rejection(session_id, run_id, body.preview_id, body.reason)
    except (sqlite3.OperationalError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return view(run)


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

    def previous(run: dict[str, Any]) -> dict[str, Any] | None:
        """按预览身份与工具版本查找已落库的过滤尝试。"""
        return next(
            (
                a
                for a in run.get("attempts", [])
                if a["preview_id"] == body.preview_id and a["tool_version"] == FILTER_TOOL_VERSION
            ),
            None,
        )

    def prepare(run: dict[str, Any], attempt_id: str) -> StageClaim:
        """锁内校验当前批准预览，并给出 attempt 目录与领取计划。"""
        preview = next((p for p in run["previews"] if p["preview_id"] == body.preview_id), None)
        if (
            preview is None
            or preview["status"] != "approved"
            or run["active_preview_id"] != body.preview_id
        ):
            raise ValueError("仅可执行当前已批准选择")
        root = run_qc_root(request.app.state.agent, run)
        verified_selection(run, root, preview)
        directory_parent = root.parent / "sc_filter"
        directory = directory_parent / attempt_id
        return StageClaim(
            directory_parent=directory_parent,
            directory=directory,
            start=lambda: store.start_sc_filter(
                session_id,
                run_id,
                run["revision"],
                body.preview_id,
                preview["binding_sha256"],
                FILTER_TOOL_VERSION,
                str(directory),
                attempt_id,
            ),
            execute=lambda attempt: run_sc_filter(
                run,
                preview,
                root,
                directory,
                request.app.state.agent.workflow_manager.r_executor,
            ),
            finish=lambda result: store.finish_sc_filter(attempt_id, result),
            on_error=lambda exc: {
                "status": "failed",
                "stage": "filter_review",
                "scientific_status": "unreviewed",
                "error": str(exc),
            },
        )

    try:
        return run_authorized(
            session_id=session_id,
            load=lambda: require_run(store, session_id, run_id),
            previous=previous,
            prepare=prepare,
            refresh=lambda: view(require_run(store, session_id, run_id)),
            unsafe_directory="过滤输出目录非法",
        )
    except (OSError, ValueError, sqlite3.OperationalError) as exc:
        raise HTTPException(409, str(exc)) from exc
