"""邻居图/聚类/UMAP 独立授权，只读恢复不执行或调用 LLM/KG。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field, field_validator

from src.analysis.qc_preview import _identity
from src.analysis.sc_cluster import (
    CLUSTER_TOOL_VERSION,
    PROTOCOL,
    checked_completed_cluster,
    checked_source,
    execution_binding,
    run_sc_cluster,
)
from src.analysis.sc_marker_reviews import CLUSTER_RECORD_VERSION, review_view
from src.api import routes_pca
from src.api.authorized_run import StageClaim, run_authorized
from src.api.deps import StoreDep
from src.api.routes_pca import dimension_view, require_parent, require_pca
from src.api.routes_qc import require_run, run_qc_root
from src.api.stage_runtime import RUNNING, has_running, running_stages
from src.ui.run_snapshot import RunSnapshot
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/sessions/{session_id}/qc/{run_id}/clusters", tags=["clusters"])
logger = logging.getLogger(__name__)
EVIDENCE_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    OverflowError,
    StopIteration,
    IndexError,
)


def view(
    run: RunSnapshot, parent: dict[str, Any], pca: dict[str, Any], root: Path
) -> dict[str, Any]:
    """新授权看 current；历史展示看 attempt 快照，不依赖被自身推进的 revision。"""
    state = dimension_view(run, parent, pca, root)
    blocked: list[str] = []
    auth_snapshot = None
    if state["current"]:
        try:
            source = checked_source(
                run, parent, pca, state["current"], root, routes_pca.DIMENSION_RECORD_VERSION
            )
            auth_snapshot = {
                "evidence": run["evidence"],
                "parent": parent,
                "pca": pca,
                "review": state["current"],
                "source": source,
            }
        except EVIDENCE_ERRORS as exc:
            safe = {
                "需要当前有效的至少 2 个连续前缀 PCs；请回上游明确记录，不自动补维",
                "本项目固定 30-NN 协议要求至少 31 细胞；不缩邻居数或放宽 QC",
                "所选 PCs 存在零范数、非有限或退化列；固定 cosine 协议不适用",
            }
            blocked.append(str(exc) if str(exc) in safe else "PCA 来源不可读取或结构不符")
    attempts = run.cluster_attempts(pca["attempt_id"])
    latest = attempts[-1] if attempts else None
    if not state["current"] and latest is None:
        blocked.extend(state["blocked"] or ["无当前有效维度记录；请回上游明确记录至少 2 个 PCs"])
    display_snapshot = auth_snapshot or (latest["snapshot"] if latest else None)
    cluster_running = "cluster" in running_stages(run)
    if has_running(run, ("filter", "pca", "marker")) or (
        cluster_running and latest is not None and latest["status"] != RUNNING
    ):
        blocked.append("该 run 有阶段运行中，不能新授权")
    attempts_view = []
    for a in attempts:
        checked = None
        invalid = None
        if a["status"] == "completed":
            try:
                checked = checked_completed_cluster(
                    run, a, root, routes_pca.DIMENSION_RECORD_VERSION
                )
            except EVIDENCE_ERRORS:
                invalid = "来源、最新维度记录、协议或产物已变化；本次结果仅为历史"
        attempts_view.append(
            {
                **{
                    k: a[k]
                    for k in (
                        "attempt_id",
                        "review_id",
                        "binding_sha256",
                        "status",
                        "created_at",
                        "finished_at",
                        "source_revision",
                        "claim_revision",
                    )
                },
                "dims": a["snapshot"]["review"]["dims"],
                "invalidated_reason": invalid,
                "result": {
                    k: checked[k]
                    for k in (
                        "cells",
                        "clusters",
                        "cluster_sizes",
                        "n_epochs",
                        "versions",
                        "seurat_threads",
                    )
                }
                if checked
                else None,
                "error": "执行失败，本地证据保留；不会自动重跑"
                if a["status"] == "failed"
                else None,
            }
        )
    return {
        "run_id": run["run_id"],
        "pca_attempt_id": pca["attempt_id"],
        "revision": run["revision"],
        "protocol": PROTOCOL,
        "tool_version": CLUSTER_TOOL_VERSION,
        "stage": "cluster_review",
        "scientific_status": "unreviewed",
        "binding_sha256": execution_binding(run, auth_snapshot)
        if auth_snapshot and not blocked
        else None,
        "selection": {
            k: display_snapshot["review"][k] for k in ("review_id", "dims", "record_version")
        }
        if display_snapshot
        else None,
        "source_summary": {
            k: display_snapshot["source"][k] for k in ("cells", "actual_pcs", "n_epochs")
        }
        if display_snapshot
        else None,
        "blocked": blocked,
        "attempts": attempts_view,
    }


@router.get("/{filter_attempt_id}/{pca_attempt_id}")
def get_clusters(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """只读本会话完整关联与阶段历史。"""
    try:
        run = require_run(store, session_id, run_id)
        return view(
            run,
            require_parent(run, filter_attempt_id),
            require_pca(run, filter_attempt_id, pca_attempt_id),
            run_qc_root(request.app.state.agent, run),
        )
    except sqlite3.Error as exc:
        logger.error("cluster state database read failed: %s", type(exc).__name__)
        raise HTTPException(409, "聚类状态读取失败，请检查本地数据库") from exc


class ClusterRequest(routes_pca.PcaRequest):
    """仅服务器绑定、确切 review、CAS revision 与独立授权依据。"""

    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    dimension_review_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@router.post("/{filter_attempt_id}/{pca_attempt_id}/execute")
def execute_clusters(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    body: ClusterRequest,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """同绑定先返历史；首次短事务认领，事务外固定执行，无重试接口。"""

    def locate(run: RunSnapshot) -> tuple[dict[str, Any], dict[str, Any], Path]:
        """按父过滤与 PCA 定位本 run 的完整上游链。"""
        parent = require_parent(run, filter_attempt_id)
        pca = require_pca(run, filter_attempt_id, pca_attempt_id)
        return parent, pca, run_qc_root(request.app.state.agent, run)

    def previous(run: RunSnapshot) -> dict[str, Any] | None:
        """按 PCA 尝试、来源绑定与工具版本查找已落库的聚类尝试。"""
        locate(run)
        reviews = run.dimension_reviews(pca_attempt_id)
        if not any(d["review_id"] == body.dimension_review_id for d in reviews):
            raise HTTPException(404, "维度记录关联不存在")
        found = next(
            (
                a
                for a in run.cluster_attempts(pca_attempt_id)
                if a["binding_sha256"] == body.binding_sha256
                and a["tool_version"] == CLUSTER_TOOL_VERSION
            ),
            None,
        )
        if found is not None and found["review_id"] != body.dimension_review_id:
            raise HTTPException(409, "维度记录与历史授权不符")
        return found

    def prepare(run: RunSnapshot, attempt_id: str) -> StageClaim:
        """锁内重读维度授权与来源证据，并给出聚类领取计划。"""
        parent, pca, root = locate(run)
        state = dimension_view(run, parent, pca, root)
        review = state["current"]
        if state["blocked"] or not review or review["review_id"] != body.dimension_review_id:
            raise HTTPException(409, "维度记录已失效，请刷新后重新记录")
        source = checked_source(run, parent, pca, review, root, routes_pca.DIMENSION_RECORD_VERSION)
        snapshot = {
            "evidence": run["evidence"],
            "parent": parent,
            "pca": pca,
            "review": review,
            "source": source,
        }
        if execution_binding(run, snapshot) != body.binding_sha256:
            raise HTTPException(409, "聚类来源绑定已变化，请刷新")
        directory_parent = root.parent / "sc_clusters"
        directory = directory_parent / attempt_id
        claimed: dict[str, Any] = {}

        def execute(attempt: dict[str, Any]) -> dict[str, Any]:
            """固定执行；未预期错误也落为失败而不泄漏本地细节。"""
            claimed["attempt"] = attempt
            try:
                return run_sc_cluster(
                    run, attempt, root, request.app.state.agent.workflow_manager.r_executor
                )
            except OSError:
                raise
            except Exception as exc:
                logger.error("unexpected cluster execution failure: %s", type(exc).__name__)
                return {
                    "status": "failed",
                    "stage": "cluster_review",
                    "scientific_status": "unreviewed",
                    "error": "聚类执行出现未预期错误；本地证据保留，请人工检查",
                }

        def finish(result: dict[str, Any]) -> dict[str, Any]:
            """保存终态并返回数据库权威结果。"""
            return store.stages.finish_cluster(attempt_id, result, claimed["attempt"])

        return StageClaim(
            directory_parent=directory_parent,
            directory=directory,
            start=lambda: store.stages.claim_cluster(
                session_id,
                run_id,
                body.revision,
                snapshot,
                body.binding_sha256,
                CLUSTER_TOOL_VERSION,
                PROTOCOL,
                body.reason,
                str(directory),
                attempt_id,
            ),
            execute=execute,
            finish=finish,
            on_error=lambda exc: {
                "status": "failed",
                "stage": "cluster_review",
                "scientific_status": "unreviewed",
                "error": "聚类产物落盘失败",
            },
        )

    def refresh() -> dict[str, Any]:
        """重读同一 run 并返回聚类只读视图。"""
        run = require_run(store, session_id, run_id)
        return view(run, *locate(run))

    try:
        return run_authorized(
            session_id=session_id,
            load=lambda: require_run(store, session_id, run_id),
            previous=previous,
            prepare=prepare,
            refresh=refresh,
            unsafe_directory="聚类输出目录不可认领",
        )
    except sqlite3.Error as exc:
        logger.error("cluster state database write failed: %s", type(exc).__name__)
        raise HTTPException(409, "聚类状态持久化失败，请只读刷新；不要重复执行") from exc
    except EVIDENCE_ERRORS as exc:
        raise HTTPException(409, "聚类来源或本地证据不符，请只读刷新并检查上游") from exc


@router.get("/{filter_attempt_id}/{pca_attempt_id}/{attempt_id}/diagnostic")
def diagnostic(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    attempt_id: str,
    request: Request,
    store: StoreDep,
) -> Response:
    """只返回完整校验后的同一份 PNG 字节，不提供逐细胞下载。"""
    try:
        run = require_run(store, session_id, run_id)
        require_parent(run, filter_attempt_id)
        require_pca(run, filter_attempt_id, pca_attempt_id)
        attempt = run.attempt("cluster", attempt_id, pca_attempt_id)
        if attempt is None or attempt["status"] != "completed":
            raise HTTPException(404, "聚类图未完成或不存在")
        checked_completed_cluster(
            run,
            attempt,
            run_qc_root(request.app.state.agent, run),
            routes_pca.DIMENSION_RECORD_VERSION,
        )
        path = Path(attempt["directory"]).resolve() / "umap_clusters.png"
        data = path.read_bytes()
        if {"path": str(path), **_identity(data)} != attempt["result"]["artifacts"][path.name]:
            raise ValueError("图身份变化")
    except sqlite3.Error as exc:
        logger.error("cluster diagnostic database read failed: %s", type(exc).__name__)
        raise HTTPException(409, "聚类状态读取失败，请检查本地数据库") from exc
    except EVIDENCE_ERRORS as exc:
        raise HTTPException(409, "聚类图或来源身份已变化；请检查本地证据") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


def require_cluster(run: RunSnapshot, pca_attempt_id: str, attempt_id: str) -> dict[str, Any]:
    """只定位本 run 与 PCA 下的确切聚类尝试。"""
    cluster = run.attempt("cluster", attempt_id, pca_attempt_id)
    if cluster is None:
        raise HTTPException(404, "聚类关联不存在")
    return cluster


class ClusterReviewRequest(routes_pca.PcaRequest):
    """只记录严格 true 的探索性接受，不接受类型名称或分析参数。"""

    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    accepted: bool = Field(strict=True)

    @field_validator("accepted")
    @classmethod
    def accept_only(cls, value: bool) -> bool:
        """拒绝含糊接受和科学批准替代字段。"""
        if value is not True:
            raise ValueError("只有明确的探索性接受可记录")
        return value


class ClusterRejectionRequest(routes_pca.PcaRequest):
    """拒绝只绑当前证据与 revision，不接受分析参数。"""

    revision: int = Field(strict=True, ge=0, le=2**63 - 1)


@router.get("/{filter_attempt_id}/{pca_attempt_id}/{attempt_id}/review")
def get_review(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    attempt_id: str,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """只读证据与本地人工事实；不执行 R、LLM 或 KG。"""
    return _review_view(
        store, session_id, run_id, filter_attempt_id, pca_attempt_id, attempt_id, request
    )


def _review_view(
    store: SessionStore,
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    attempt_id: str,
    request: Request,
) -> dict[str, Any]:
    """按同一 store 重读 run 后返回聚类复核只读视图。"""
    try:
        run = require_run(store, session_id, run_id)
        require_parent(run, filter_attempt_id)
        require_pca(run, filter_attempt_id, pca_attempt_id)
        return review_view(
            run,
            require_cluster(run, pca_attempt_id, attempt_id),
            run_qc_root(request.app.state.agent, run),
        )
    except sqlite3.Error as exc:
        logger.error("cluster review database read failed: %s", type(exc).__name__)
        raise HTTPException(409, "聚类复核读取失败，请只读刷新") from exc


@router.post("/{filter_attempt_id}/{pca_attempt_id}/{attempt_id}/review")
def record_review(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    attempt_id: str,
    body: ClusterReviewRequest,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """追加接受事实；旧请求重发仅返回历史，不重新激活或授权 marker。"""
    try:
        run = require_run(store, session_id, run_id)
        require_parent(run, filter_attempt_id)
        require_pca(run, filter_attempt_id, pca_attempt_id)
        cluster = require_cluster(run, pca_attempt_id, attempt_id)
        root = run_qc_root(request.app.state.agent, run)
        previous = next(
            (
                d
                for d in run.cluster_reviews(attempt_id)
                if d["binding_sha256"] == body.binding_sha256
                and d["record_version"] == CLUSTER_RECORD_VERSION
                and d["source_revision"] == body.revision
            ),
            None,
        )
        if previous:
            if previous["reason"] != body.reason or previous["accepted"] is not True:
                raise HTTPException(409, "同一 revision 的聚类复核内容冲突")
            return review_view(run, cluster, root)
        state = review_view(run, cluster, root)
        if not state["binding_sha256"] or state["binding_sha256"] != body.binding_sha256:
            raise HTTPException(409, "聚类证据不可记录或已变化，请只读刷新")
        store.stages.record_cluster_review(
            session_id,
            run,
            cluster,
            body.binding_sha256,
            CLUSTER_RECORD_VERSION,
            body.revision,
            body.reason,
        )
        return _review_view(
            store, session_id, run_id, filter_attempt_id, pca_attempt_id, attempt_id, request
        )
    except sqlite3.Error as exc:
        logger.error("cluster review database write failed: %s", type(exc).__name__)
        raise HTTPException(409, "聚类复核写入失败，请只读刷新") from exc
    except ValueError as exc:
        raise HTTPException(409, "聚类复核或父身份冲突，请只读刷新") from exc


@router.post("/{filter_attempt_id}/{pca_attempt_id}/{attempt_id}/reject")
def reject_review(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    attempt_id: str,
    body: ClusterRejectionRequest,
    request: Request,
    store: StoreDep,
) -> dict[str, Any]:
    """追加拒绝事实；不改写接受历史，不授权 marker。"""
    try:
        run = require_run(store, session_id, run_id)
        require_parent(run, filter_attempt_id)
        require_pca(run, filter_attempt_id, pca_attempt_id)
        cluster = require_cluster(run, pca_attempt_id, attempt_id)
        root = run_qc_root(request.app.state.agent, run)
        state = review_view(run, cluster, root)
        if not state["binding_sha256"] or state["binding_sha256"] != body.binding_sha256:
            raise HTTPException(409, "聚类证据不可记录或已变化，请只读刷新")
        store.stages.record_cluster_rejection(
            session_id,
            run,
            cluster,
            cluster["binding_sha256"],
            CLUSTER_RECORD_VERSION,
            body.revision,
            body.reason,
        )
        return _review_view(
            store, session_id, run_id, filter_attempt_id, pca_attempt_id, attempt_id, request
        )
    except sqlite3.Error as exc:
        logger.error("cluster rejection database write failed: %s", type(exc).__name__)
        raise HTTPException(409, "聚类拒绝写入失败，请只读刷新") from exc
    except ValueError as exc:
        raise HTTPException(409, "聚类拒绝或父身份冲突，请只读刷新") from exc
