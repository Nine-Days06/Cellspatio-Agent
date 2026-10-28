"""邻居图/聚类/UMAP 独立授权，只读恢复不执行或调用 LLM/KG。"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

from src.analysis.qc_preview import _identity
from src.analysis.sc_cluster import (
    CLUSTER_TOOL_VERSION,
    PROTOCOL,
    checked_completed_cluster,
    checked_source,
    execution_binding,
    run_sc_cluster,
)
from src.api import routes_pca
from src.api.chat_service import session_lock
from src.api.routes_pca import dimension_view, require_parent, require_pca
from src.api.routes_qc import qc_root, require_run
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
    run: dict[str, Any], parent: dict[str, Any], pca: dict[str, Any], root: Path
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
    attempts = [a for a in run["cluster_attempts"] if a["pca_attempt_id"] == pca["attempt_id"]]
    latest = attempts[-1] if attempts else None
    if not state["current"] and latest is None:
        blocked.extend(state["blocked"] or ["无当前有效维度记录；请回上游明确记录至少 2 个 PCs"])
    display_snapshot = auth_snapshot or (latest["snapshot"] if latest else None)
    cluster_running = any(a["status"] == "running" for a in run["cluster_attempts"])
    if any(a["status"] == "running" for a in [*run["attempts"], *run["pca_attempts"]]) or (
        cluster_running and latest is not None and latest["status"] != "running"
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
    session_id: str, run_id: str, filter_attempt_id: str, pca_attempt_id: str, request: Request
) -> dict[str, Any]:
    """只读本会话完整关联与阶段历史。"""
    try:
        run = require_run(SessionStore(), session_id, run_id)
        return view(
            run,
            require_parent(run, filter_attempt_id),
            require_pca(run, filter_attempt_id, pca_attempt_id),
            qc_root(request.app.state.agent),
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
) -> dict[str, Any]:
    """同绑定先返历史；首次短事务认领，事务外固定执行，无重试接口。"""
    store = SessionStore()
    lock = session_lock(session_id)
    acquired = False
    try:
        run = require_run(store, session_id, run_id)
        parent = require_parent(run, filter_attempt_id)
        pca = require_pca(run, filter_attempt_id, pca_attempt_id)
        root = qc_root(request.app.state.agent)
        reviews = [d for d in run["dimension_reviews"] if d["pca_attempt_id"] == pca_attempt_id]
        if not any(d["review_id"] == body.dimension_review_id for d in reviews):
            raise HTTPException(404, "维度记录关联不存在")
        previous = next(
            (
                a
                for a in run["cluster_attempts"]
                if a["pca_attempt_id"] == pca_attempt_id
                and a["binding_sha256"] == body.binding_sha256
                and a["tool_version"] == CLUSTER_TOOL_VERSION
            ),
            None,
        )
        if previous:
            if previous["review_id"] != body.dimension_review_id:
                raise HTTPException(409, "维度记录与历史授权不符")
            return view(run, parent, pca, root)
        acquired = lock.acquire(blocking=False)
        if not acquired:
            raise HTTPException(409, "该会话已有任务在进行")
        run = require_run(store, session_id, run_id)
        parent = require_parent(run, filter_attempt_id)
        pca = require_pca(run, filter_attempt_id, pca_attempt_id)
        previous = next(
            (
                a
                for a in run["cluster_attempts"]
                if a["pca_attempt_id"] == pca_attempt_id
                and a["binding_sha256"] == body.binding_sha256
                and a["tool_version"] == CLUSTER_TOOL_VERSION
            ),
            None,
        )
        if previous:
            if previous["review_id"] != body.dimension_review_id:
                raise HTTPException(409, "维度记录与历史授权不符")
            return view(run, parent, pca, root)
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
        attempt_id = uuid.uuid4().hex
        directory = root.parent / "sc_clusters" / attempt_id
        if directory.resolve().parent != root.parent / "sc_clusters" or directory.exists():
            raise HTTPException(409, "聚类输出目录不可认领")
        attempt = store.start_sc_cluster(
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
        )
        if attempt["claimed"]:
            try:
                result = run_sc_cluster(
                    run, attempt, root, request.app.state.agent.workflow_manager.r_executor
                )
            except OSError:
                result = {
                    "status": "failed",
                    "stage": "cluster_review",
                    "scientific_status": "unreviewed",
                    "error": "聚类产物落盘失败",
                }
            except Exception as exc:
                logger.error("unexpected cluster execution failure: %s", type(exc).__name__)
                result = {
                    "status": "failed",
                    "stage": "cluster_review",
                    "scientific_status": "unreviewed",
                    "error": "聚类执行出现未预期错误；本地证据保留，请人工检查",
                }
            saved = store.finish_sc_cluster(attempt_id, result, attempt)
            if saved != result:
                # DB 的失败权威不能被本地旧 completed metadata 冒充。
                (directory / "metadata.json").write_text(
                    json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return view(require_run(store, session_id, run_id), parent, pca, root)
    except sqlite3.Error as exc:
        logger.error("cluster state database write failed: %s", type(exc).__name__)
        raise HTTPException(409, "聚类状态持久化失败，请只读刷新；不要重复执行") from exc
    except EVIDENCE_ERRORS as exc:
        raise HTTPException(409, "聚类来源或本地证据不符，请只读刷新并检查上游") from exc
    finally:
        if acquired:
            lock.release()


@router.get("/{filter_attempt_id}/{pca_attempt_id}/{attempt_id}/diagnostic")
def diagnostic(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    attempt_id: str,
    request: Request,
) -> Response:
    """只返回完整校验后的同一份 PNG 字节，不提供逐细胞下载。"""
    try:
        run = require_run(SessionStore(), session_id, run_id)
        require_parent(run, filter_attempt_id)
        require_pca(run, filter_attempt_id, pca_attempt_id)
        attempt = next(
            (
                a
                for a in run["cluster_attempts"]
                if a["attempt_id"] == attempt_id and a["pca_attempt_id"] == pca_attempt_id
            ),
            None,
        )
        if attempt is None or attempt["status"] != "completed":
            raise HTTPException(404, "聚类图未完成或不存在")
        checked_completed_cluster(
            run, attempt, qc_root(request.app.state.agent), routes_pca.DIMENSION_RECORD_VERSION
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
