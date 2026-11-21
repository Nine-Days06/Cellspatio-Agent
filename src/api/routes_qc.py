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
from src.api.deps import StoreDep
from src.api.stage_history import history_entry
from src.api.stage_runtime import running_stages
from src.ui.analysis_roots import AnalysisRoots
from src.ui.run_snapshot import RunSnapshot
from src.ui.sc_stage_defs import FILTER_STAGE, stage_directory, stage_failure
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/sessions/{session_id}/qc", tags=["qc"])


def run_qc_root(roots: AnalysisRoots, run: RunSnapshot) -> Path:
    """由应用注入的 roots 校验并返回 run 的 QC 根。"""
    return roots.qc_root(run)


def require_run(store: SessionStore, session_id: str, run_id: str) -> RunSnapshot:
    """关联缺失/跨会话统一 404，不扫描旧消息路径。"""
    run = store.get_qc_run(session_id, run_id)
    if run is None:
        raise HTTPException(404, "QC 关联不存在；旧证据需重新 QC 才能交互")
    return run


def view(run: RunSnapshot, blocked: str | None = None) -> dict[str, Any]:
    """只返回界面需要的权威状态，不返回原输入或逐细胞内容。"""
    running = running_stages(run)
    return {
        "run_id": run["run_id"],
        "revision": run["revision"],
        "qc": run["evidence"]["summary"],
        "active_preview": run.active_preview,
        "approvals": [p for p in run.previews if p["status"] == "approved"],
        "blocked": [blocked] if blocked else [],
        "pca_running": "pca" in running,
        "cluster_running": "cluster" in running,
        "marker_running": "marker" in running,
        "attempts": [
            history_entry("filter", attempt, result=attempt["result"])
            for attempt in run.stage_attempts("filter")
        ],
    }


@router.get("/{run_id}")
def get_qc(session_id: str, run_id: str, request: Request, store: StoreDep) -> dict[str, Any]:
    """刷新后仍显示精确批准历史；变化证据显示阻断。"""
    run = require_run(store, session_id, run_id)
    try:
        checked_evidence(run, run_qc_root(request.app.state.analysis_roots, run))
        active = run.active_preview
        if active and active["status"] == "pending":
            check_preview(run, run_qc_root(request.app.state.analysis_roots, run), active)
    except (OSError, ValueError) as exc:
        return view(run, str(exc))
    return view(run)


@router.post("/{run_id}/preview")
def preview_qc(
    session_id: str, run_id: str, body: Thresholds, request: Request, store: StoreDep
) -> dict[str, Any]:
    """生成独立预览，以数据库版本条件替换 active 预览。"""
    run = require_run(store, session_id, run_id)
    try:
        preview = build_preview(run, run_qc_root(request.app.state.analysis_roots, run), body)
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
    session_id: str, run_id: str, body: ApprovalRequest, request: Request, store: StoreDep
) -> dict[str, Any]:
    """验证证据后短事务批准；已批准请求幂等返回历史，不再次计算。"""
    run = require_run(store, session_id, run_id)
    preview = next((p for p in run.previews if p["preview_id"] == body.preview_id), None)
    if preview is None:
        raise HTTPException(409, "预览不存在，请重新预览")
    try:
        if preview["status"] != "approved":
            check_preview(run, run_qc_root(request.app.state.analysis_roots, run), preview)
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
def reject_qc(
    session_id: str, run_id: str, body: RejectionRequest, store: StoreDep
) -> dict[str, Any]:
    """记录当前待批准预览的拒绝；已批准预览与已执行 run 不可拒绝。"""
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
    session_id: str, run_id: str, body: FilterRequest, request: Request, store: StoreDep
) -> dict[str, Any]:
    """固定过滤执行；重复授权只返回历史，不调用 R。"""

    def previous(run: RunSnapshot) -> dict[str, Any] | None:
        """按预览身份与工具版本查找已落库的过滤尝试。"""
        return next(
            (
                a
                for a in run.stage_attempts("filter")
                if a["preview_id"] == body.preview_id and a["tool_version"] == FILTER_TOOL_VERSION
            ),
            None,
        )

    def prepare(run: RunSnapshot, attempt_id: str) -> StageClaim:
        """锁内校验当前批准预览，并给出 attempt 目录与领取计划。"""
        preview = run.active_preview
        if (
            preview is None
            or preview["preview_id"] != body.preview_id
            or preview["status"] != "approved"
        ):
            raise ValueError("仅可执行当前已批准选择")
        root = run_qc_root(request.app.state.analysis_roots, run)
        verified_selection(run, root, preview)
        directory_parent, directory = stage_directory(FILTER_STAGE, root, attempt_id)
        return StageClaim(
            directory_parent=directory_parent,
            directory=directory,
            start=lambda: store.stages.claim_filter(
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
            finish=lambda result: store.stages.finish_filter(attempt_id, result),
            on_error=lambda exc: stage_failure(FILTER_STAGE, str(exc)),
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
