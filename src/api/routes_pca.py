"""独立 PCA 授权和只读本地诊断；不接聊天解释器。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.analysis.qc_preview import _identity
from src.analysis.sc_pca import (
    DIMENSION_RECORD_VERSION,
    PCA_TOOL_VERSION,
    PROTOCOL,
    checked_completed_pca,
    checked_parent,
    current_dimensions,
    dimension_binding,
    run_sc_pca,
)
from src.api.authorized_run import StageClaim, run_authorized
from src.api.deps import StoreDep
from src.api.routes_qc import require_run, run_qc_root
from src.api.stage_runtime import has_running
from src.ui.run_snapshot import RunSnapshot
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/sessions/{session_id}/qc/{run_id}/pca", tags=["pca"])
logger = logging.getLogger(__name__)


def require_parent(run: RunSnapshot, filter_attempt_id: str) -> dict[str, Any]:
    """仅定位已关联父记录；路径不能来自客户端。"""
    parent = next((a for a in run.filter_attempts if a["attempt_id"] == filter_attempt_id), None)
    if parent is None:
        raise HTTPException(404, "父过滤关联不存在")
    return parent


def view(run: RunSnapshot, parent: dict[str, Any], root: Path) -> dict[str, Any]:
    """返回方法/授权状态和必要汇总，不返回逐细胞数据或私有路径。"""
    blocked = []
    source = None
    try:
        source = checked_parent(run, parent, root)
        if source["cells"] < 3:
            blocked.append("PCA 至少需要 3 个过滤后细胞；不放宽 QC")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        blocked.append(str(exc))
    attempts = run.pca_attempts(parent["attempt_id"])
    if has_running(run):
        blocked.append("该 run 有阶段运行中")
    return {
        "run_id": run["run_id"],
        "filter_attempt_id": parent["attempt_id"],
        "protocol": PROTOCOL,
        "tool_version": PCA_TOOL_VERSION,
        "binding_sha256": source["binding_sha256"] if source else None,
        "source_summary": {k: source[k] for k in ("cells", "features")} if source else None,
        "blocked": blocked,
        "attempts": [
            {
                **{
                    k: a[k]
                    for k in ("attempt_id", "binding_sha256", "status", "created_at", "finished_at")
                },
                "result": {
                    k: v
                    for k, v in a["result"].items()
                    if k
                    in (
                        "cells",
                        "features",
                        "selected_features",
                        "usable_features",
                        "actual_pcs",
                        "pc_reduction_reason",
                        "versions",
                        "error",
                        "elapsed_seconds",
                        "scientific_status",
                    )
                }
                if a["result"]
                else None,
            }
            for a in attempts
        ],
    }


@router.get("/{filter_attempt_id}")
def get_pca(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """GET 仅校验和恢复，不启动分析。"""
    run = require_run(store, session_id, run_id)
    return view(
        run,
        require_parent(run, filter_attempt_id),
        run_qc_root(request.app.state.agent, run),
    )


class PcaRequest(BaseModel):
    """只允许已展示绑定与本地授权依据。"""

    model_config = ConfigDict(extra="forbid")
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonempty(cls, value: str) -> str:
        """空白不是授权依据。"""
        if not value.strip():
            raise ValueError("请输入授权依据")
        return value.strip()


@router.post("/{filter_attempt_id}")
def execute_pca(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    body: PcaRequest,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """同绑定终态/运行态只返历史；首次请求独立校验和原子认领。"""

    def previous(run: RunSnapshot) -> dict[str, Any] | None:
        """按父过滤、来源绑定与工具版本查找已落库的 PCA 尝试。"""
        require_parent(run, filter_attempt_id)
        return next(
            (
                a
                for a in run.pca_attempts(filter_attempt_id)
                if a["binding_sha256"] == body.binding_sha256
                and a["tool_version"] == PCA_TOOL_VERSION
            ),
            None,
        )

    def prepare(run: RunSnapshot, attempt_id: str) -> StageClaim:
        """锁内重读父关联与来源证据，并给出 PCA 领取计划。"""
        parent = require_parent(run, filter_attempt_id)
        root = run_qc_root(request.app.state.agent, run)
        source = checked_parent(run, parent, root)
        if source["binding_sha256"] != body.binding_sha256 or source["cells"] < 3:
            raise ValueError("PCA 绑定不符或细胞不足，请刷新")
        directory_parent = root.parent / "sc_pca"
        directory = directory_parent / attempt_id
        return StageClaim(
            directory_parent=directory_parent,
            directory=directory,
            start=lambda: store.stages.claim_pca(
                session_id,
                run_id,
                run["revision"],
                filter_attempt_id,
                body.binding_sha256,
                PCA_TOOL_VERSION,
                PROTOCOL,
                body.reason,
                str(directory),
                attempt_id,
            ),
            execute=lambda attempt: run_sc_pca(
                run,
                parent,
                root,
                directory,
                body.binding_sha256,
                request.app.state.agent.workflow_manager.r_executor,
            ),
            finish=lambda result: store.stages.finish_pca(attempt_id, result),
            on_error=lambda exc: {
                "status": "failed",
                "stage": "pca_review",
                "scientific_status": "unreviewed",
                "error": str(exc),
            },
        )

    def refresh() -> dict[str, Any]:
        """重读同一 run 并返回 PCA 只读视图。"""
        run = require_run(store, session_id, run_id)
        return view(
            run, require_parent(run, filter_attempt_id), run_qc_root(request.app.state.agent, run)
        )

    try:
        return run_authorized(
            session_id=session_id,
            load=lambda: require_run(store, session_id, run_id),
            previous=previous,
            prepare=prepare,
            refresh=refresh,
            unsafe_directory="PCA 输出目录非法",
        )
    except (OSError, ValueError, KeyError, TypeError, sqlite3.OperationalError) as exc:
        logger.error("PCA execution rejected: %s", type(exc).__name__)
        raise HTTPException(409, str(exc)) from exc


def require_pca(run: RunSnapshot, filter_attempt_id: str, attempt_id: str) -> dict[str, Any]:
    """只定位本会话关联的 PCA，不能借用其他父记录。"""
    attempt = next(
        (a for a in run.pca_attempts(filter_attempt_id) if a["attempt_id"] == attempt_id),
        None,
    )
    if attempt is None:
        raise HTTPException(404, "PCA 关联不存在")
    return attempt


def dimension_view(
    run: RunSnapshot, parent: dict[str, Any], attempt: dict[str, Any], root: Path
) -> dict[str, Any]:
    """恢复当前选择及失效历史；不返回原始细胞表或私有产物路径。"""
    evidence = None
    binding = None
    blocked = []
    try:
        checked = checked_completed_pca(run, parent, attempt, root)
        binding = dimension_binding(run, parent, attempt, checked, DIMENSION_RECORD_VERSION)
        evidence = {k: checked[k] for k in ("actual_pcs", "stdev")}
    except OSError:
        blocked.append("PCA 或父证据文件不可读取，请检查本地产物")
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        blocked.append(str(exc))
    if has_running(run):
        blocked.append("该 run 有阶段运行中，不能记录维度")
    history = run.dimension_reviews(attempt["attempt_id"])
    latest = history[-1] if history else None
    current = (
        None if blocked else current_dimensions(run, attempt, binding, DIMENSION_RECORD_VERSION)
    )
    return {
        "run_id": run["run_id"],
        "filter_attempt_id": parent["attempt_id"],
        "attempt_id": attempt["attempt_id"],
        "record_version": DIMENSION_RECORD_VERSION,
        "stage": "pca_review",
        "scientific_status": "unreviewed",
        "revision": run["revision"],
        "binding_sha256": binding if not blocked else None,
        "evidence": evidence,
        "blocked": blocked,
        "current": current,
        "history": [
            {
                **d,
                "invalidated_reason": None
                if d == current
                else (
                    "已有更新的人工记录"
                    if d != latest
                    else "PCA 身份、父选择、记录版本或 run revision 已变化；需重新明确记录"
                ),
            }
            for d in history
        ],
    }


@router.get("/{filter_attempt_id}/{attempt_id}/dimensions")
def get_dimensions(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    attempt_id: str,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """只读证据和人工事实，不调用 R、LLM 或 KG。"""
    return _dimensions_view(store, session_id, run_id, filter_attempt_id, attempt_id, request)


def _dimensions_view(
    store: SessionStore,
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    attempt_id: str,
    request: Request,
) -> dict[str, Any]:
    """按同一 store 重读 run 后返回维度只读视图。"""
    run = require_run(store, session_id, run_id)
    return dimension_view(
        run,
        require_parent(run, filter_attempt_id),
        require_pca(run, filter_attempt_id, attempt_id),
        run_qc_root(request.app.state.agent, run),
    )


class DimensionRequest(PcaRequest):
    """首版仅手工前缀 1..k；严格整数，不接受隐含类型转换。"""

    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    n_dims: int = Field(strict=True, ge=1, le=PROTOCOL["pca"]["npcs_max"])


@router.post("/{filter_attempt_id}/{attempt_id}/dimensions")
def record_dimensions(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    attempt_id: str,
    body: DimensionRequest,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """显式条件写入；旧请求重发只能返回历史，绝不重新激活。"""
    run = require_run(store, session_id, run_id)
    parent = require_parent(run, filter_attempt_id)
    attempt = require_pca(run, filter_attempt_id, attempt_id)
    root = run_qc_root(request.app.state.agent, run)
    previous = next(
        (
            d
            for d in run.dimension_reviews(attempt_id)
            if d["binding_sha256"] == body.binding_sha256
            and d["record_version"] == DIMENSION_RECORD_VERSION
            and d["source_revision"] == body.revision
        ),
        None,
    )
    if previous:
        if previous["dims"] != list(range(1, body.n_dims + 1)) or previous["reason"] != body.reason:
            raise HTTPException(409, "同一 revision 的记录内容冲突，请刷新")
        return dimension_view(run, parent, attempt, root)
    state = dimension_view(run, parent, attempt, root)
    if not state["binding_sha256"] or body.binding_sha256 != state["binding_sha256"]:
        raise HTTPException(409, "PCA 证据绑定不可记录或已变化，请刷新")
    if body.n_dims > state["evidence"]["actual_pcs"]:
        raise HTTPException(422, "维度数不能超过已计算的 actual_pcs")
    try:
        store.stages.record_dimensions(
            session_id,
            run,
            parent,
            attempt,
            body.binding_sha256,
            DIMENSION_RECORD_VERSION,
            body.revision,
            list(range(1, body.n_dims + 1)),
            body.reason,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except sqlite3.OperationalError as exc:
        logger.exception("PCA dimension record database write failed")
        raise HTTPException(409, "维度记录写入失败，请检查本地数据库并只读刷新") from exc
    return _dimensions_view(store, session_id, run_id, filter_attempt_id, attempt_id, request)


@router.get("/{filter_attempt_id}/{attempt_id}/diagnostic")
def diagnostic(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    attempt_id: str,
    request: Request,
    store: StoreDep,
) -> Response:
    """验证登记 PNG 的同一份字节后返回，不接受任意路径。"""
    run = require_run(store, session_id, run_id)
    require_parent(run, filter_attempt_id)
    attempt = next(
        (a for a in run.pca_attempts(filter_attempt_id) if a["attempt_id"] == attempt_id),
        None,
    )
    if attempt is None or attempt["status"] != "completed":
        raise HTTPException(404, "PCA 诊断未完成或不存在")
    directory = Path(attempt["directory"]).resolve()
    root = run_qc_root(request.app.state.agent, run).parent / "sc_pca"
    path = directory / "pca_diagnostics.png"
    try:
        if directory != root / attempt_id or path.resolve().parent != directory:
            raise ValueError("PCA 诊断路径非法")
        data = path.read_bytes()
        identity = attempt["result"]["artifacts"]["pca_diagnostics.png"]
        if {"path": str(path), **_identity(data)} != identity or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("PCA 诊断身份已变化")
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})
