"""聚类接受后的独立 marker 授权、恢复与诊断；不经 LLM 或 KG。"""

import csv
import io
import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field

from src.analysis.qc_preview import _identity
from src.analysis.sc_marker_reviews import review_view
from src.analysis.sc_markers import (
    MARKER_FIELDS,
    MARKER_TOOL_VERSION,
    PROTOCOL,
    checked_completed_marker,
    checked_source,
    execution_binding,
    run_sc_markers,
)
from src.api.chat_service import session_lock
from src.api.routes_clusters import EVIDENCE_ERRORS, require_cluster
from src.api.routes_pca import PcaRequest, require_parent, require_pca
from src.api.routes_qc import qc_root, require_run
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/sessions/{session_id}/qc/{run_id}/markers", tags=["markers"])
logger = logging.getLogger(__name__)


def view(run: dict[str, Any], cluster: dict[str, Any], root: Path) -> dict[str, Any]:
    """新授权看 current；结果恢复看自己的快照。所有字段均为安全汇总。"""
    state = review_view(run, cluster, root)
    attempts = [
        a for a in run["marker_attempts"] if a["cluster_attempt_id"] == cluster["attempt_id"]
    ]
    latest = attempts[-1] if attempts else None
    blocked, snapshot = [], None
    if state["current"]:
        try:
            source = checked_source(run, cluster, state["current"], root)
            snapshot = {"cluster": cluster, "review": state["current"], "source": source}
        except EVIDENCE_ERRORS as exc:
            safe = "marker 协议要求至少两簇，且每簇与其余各至少 3 细胞；不会合并小簇"
            blocked.append(safe if str(exc) == safe else "marker 来源或接受记录不符")
    elif latest is None:
        blocked.extend(
            state["blocked"] or ["需要当前有效的探索性聚类接受；不会自动接受或运行 marker"]
        )
    if any(
        a["status"] == "running"
        for key in ("attempts", "pca_attempts", "cluster_attempts")
        for a in run[key]
    ):
        blocked.append("该 run 有阶段运行中，不能授权 marker")
    if any(
        a["status"] == "running"
        for a in run["marker_attempts"]
        if a["cluster_attempt_id"] != cluster["attempt_id"]
    ):
        blocked.append("该 run 有其他 marker 运行中")
    displayed = snapshot or (latest["snapshot"] if latest else None)
    history = []
    for a in attempts:
        checked, invalid = None, None
        if a["status"] == "completed":
            try:
                checked = checked_completed_marker(run, a, root)
            except EVIDENCE_ERRORS:
                invalid = "来源、最新聚类接受、协议或产物变化；本次 marker 仅为历史"
        history.append(
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
                "invalidated_reason": invalid,
                "result": {
                    k: checked[k]
                    for k in (
                        "cells",
                        "features",
                        "clusters",
                        "marker_rows",
                        "positive_rows",
                        "negative_rows",
                        "cluster_summary",
                        "plot_features",
                        "backend",
                        "versions",
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
        "cluster_attempt_id": cluster["attempt_id"],
        "revision": run["revision"],
        "stage": "marker_review",
        "scientific_status": "unreviewed",
        "protocol": PROTOCOL,
        "tool_version": MARKER_TOOL_VERSION,
        "binding_sha256": execution_binding(run, snapshot) if snapshot and not blocked else None,
        "review": {k: displayed["review"][k] for k in ("review_id", "record_version", "accepted")}
        if displayed
        else None,
        "blocked": blocked,
        "attempts": history,
    }


def _associated(
    run: dict[str, Any], filter_attempt_id: str, pca_attempt_id: str, cluster_attempt_id: str
) -> dict[str, Any]:
    """所有父级关联都必须属于当前 run；跨会话和错配统一 404。"""
    require_parent(run, filter_attempt_id)
    require_pca(run, filter_attempt_id, pca_attempt_id)
    return require_cluster(run, pca_attempt_id, cluster_attempt_id)


@router.get("/{filter_attempt_id}/{pca_attempt_id}/{cluster_attempt_id}")
def get_markers(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    cluster_attempt_id: str,
    request: Request,
) -> dict[str, Any]:
    """GET 不认领、不执行、不恢复遗留任务。"""
    try:
        run = require_run(SessionStore(), session_id, run_id)
        return view(
            run,
            _associated(run, filter_attempt_id, pca_attempt_id, cluster_attempt_id),
            qc_root(request.app.state.agent),
        )
    except sqlite3.Error as exc:
        logger.error("marker read failed: %s", type(exc).__name__)
        raise HTTPException(409, "marker 状态读取失败，请检查本地数据库") from exc


class MarkerRequest(PcaRequest):
    """仅确切接受记录、服务端绑定、严格 revision 和本地理由。"""

    revision: int = Field(strict=True, ge=0, le=2**63 - 1)
    cluster_review_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@router.post("/{filter_attempt_id}/{pca_attempt_id}/{cluster_attempt_id}/execute")
def execute_markers(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    cluster_attempt_id: str,
    body: MarkerRequest,
    request: Request,
) -> dict[str, Any]:
    """历史请求只返回历史；首次 CAS 认领后在事务外执行一次固定 R。"""
    store, lock, acquired = SessionStore(), session_lock(session_id), False
    root = qc_root(request.app.state.agent)
    try:
        for pass_number in (0, 1):
            run = require_run(store, session_id, run_id)
            cluster = _associated(run, filter_attempt_id, pca_attempt_id, cluster_attempt_id)
            if not any(
                r["review_id"] == body.cluster_review_id
                and r["cluster_attempt_id"] == cluster_attempt_id
                for r in run["cluster_reviews"]
            ):
                raise HTTPException(404, "聚类接受关联不存在")
            previous = next(
                (
                    a
                    for a in run["marker_attempts"]
                    if a["cluster_attempt_id"] == cluster_attempt_id
                    and a["binding_sha256"] == body.binding_sha256
                    and a["tool_version"] == MARKER_TOOL_VERSION
                ),
                None,
            )
            if previous:
                if previous["review_id"] != body.cluster_review_id:
                    raise HTTPException(409, "聚类接受与历史 marker 授权不符")
                return view(run, cluster, root)
            if pass_number == 0:
                acquired = lock.acquire(blocking=False)
                if not acquired:
                    raise HTTPException(409, "该会话已有任务在进行")
        state = review_view(run, cluster, root)
        review = state["current"]
        if state["blocked"] or not review or review["review_id"] != body.cluster_review_id:
            raise HTTPException(409, "聚类接受已失效，请刷新后重新明确记录")
        source = checked_source(run, cluster, review, root)
        snapshot = {"cluster": cluster, "review": review, "source": source}
        if execution_binding(run, snapshot) != body.binding_sha256:
            raise HTTPException(409, "marker 来源绑定已变化，请刷新")
        attempt_id = uuid.uuid4().hex
        directory = root.parent / "sc_markers" / attempt_id
        if directory.resolve().parent != root.parent / "sc_markers" or directory.exists():
            raise HTTPException(409, "marker 输出目录不可认领")
        attempt = store.start_sc_markers(
            session_id,
            run_id,
            body.revision,
            snapshot,
            body.binding_sha256,
            MARKER_TOOL_VERSION,
            PROTOCOL,
            body.reason,
            str(directory),
            attempt_id,
        )
        if attempt["claimed"]:
            try:
                result = run_sc_markers(
                    run, attempt, root, request.app.state.agent.workflow_manager.r_executor
                )
            except OSError:
                result = {
                    "status": "failed",
                    "stage": "marker_review",
                    "scientific_status": "unreviewed",
                    "error": "marker 产物落盘失败",
                }
            except Exception as exc:  # noqa: BLE001 - 已认领任务必须安全收口，私有异常不进入消息。
                logger.error("unexpected marker execution failure: %s", type(exc).__name__)
                result = {
                    "status": "failed",
                    "stage": "marker_review",
                    "scientific_status": "unreviewed",
                    "error": "marker 出现未预期错误；请检查本地证据",
                }
            saved = store.finish_sc_markers(attempt_id, result, attempt)
            if saved != result:
                (directory / "metadata.json").write_text(
                    json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return view(require_run(store, session_id, run_id), cluster, root)
    except sqlite3.Error as exc:
        logger.error("marker write failed: %s", type(exc).__name__)
        raise HTTPException(409, "marker 状态持久化失败，请只读刷新；不要重复执行") from exc
    except EVIDENCE_ERRORS as exc:
        raise HTTPException(409, "marker 来源或本地证据不符，请只读刷新并检查上游") from exc
    finally:
        if acquired:
            lock.release()


@router.get("/{filter_attempt_id}/{pca_attempt_id}/{cluster_attempt_id}/{attempt_id}/diagnostic")
def diagnostic(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    cluster_attempt_id: str,
    attempt_id: str,
    request: Request,
) -> Response:
    """完整证据校验后返回同一 PNG 字节；不接受路径参数或逐细胞下载。"""
    try:
        run = require_run(SessionStore(), session_id, run_id)
        _associated(run, filter_attempt_id, pca_attempt_id, cluster_attempt_id)
        attempt = next(
            (
                a
                for a in run["marker_attempts"]
                if a["attempt_id"] == attempt_id and a["cluster_attempt_id"] == cluster_attempt_id
            ),
            None,
        )
        if not attempt or attempt["status"] != "completed":
            raise HTTPException(404, "marker 图未完成或不存在")
        checked_completed_marker(run, attempt, qc_root(request.app.state.agent))
        path = Path(attempt["directory"]).resolve() / "marker_dotplot.png"
        data = path.read_bytes()
        if {"path": str(path), **_identity(data)} != attempt["result"]["artifacts"][path.name]:
            raise ValueError("marker 图身份变化")
    except sqlite3.Error as exc:
        logger.error("marker diagnostic read failed: %s", type(exc).__name__)
        raise HTTPException(409, "marker 状态读取失败") from exc
    except EVIDENCE_ERRORS as exc:
        raise HTTPException(409, "marker 图或来源身份变化；请检查本地证据") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{filter_attempt_id}/{pca_attempt_id}/{cluster_attempt_id}/{attempt_id}/candidates")
def candidates(
    session_id: str,
    run_id: str,
    filter_attempt_id: str,
    pca_attempt_id: str,
    cluster_attempt_id: str,
    attempt_id: str,
    request: Request,
    response: Response,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> dict[str, Any]:
    """身份核对后分页返回候选统计；不返回路径、逐细胞表达或自由文本依据。"""
    try:
        run = require_run(SessionStore(), session_id, run_id)
        _associated(run, filter_attempt_id, pca_attempt_id, cluster_attempt_id)
        attempt = next(
            (
                a
                for a in run["marker_attempts"]
                if a["attempt_id"] == attempt_id and a["cluster_attempt_id"] == cluster_attempt_id
            ),
            None,
        )
        if not attempt or attempt["status"] != "completed":
            raise HTTPException(404, "marker 候选未完成或不存在")
        summary = checked_completed_marker(run, attempt, qc_root(request.app.state.agent))
        path = Path(attempt["directory"]).resolve() / "markers.csv"
        data = path.read_bytes()
        if {"path": str(path), **_identity(data)} != attempt["result"]["artifacts"][path.name]:
            raise ValueError("marker 候选身份变化")
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        response.headers["Cache-Control"] = "no-store"
        page = [
            {**{k: r[k] for k in MARKER_FIELDS[:4]}, **{k: float(r[k]) for k in MARKER_FIELDS[4:]}}
            for r in rows[offset : offset + limit]
        ]
        return {
            "attempt_id": attempt_id,
            "binding_sha256": attempt["binding_sha256"],
            "scientific_status": "unreviewed",
            "total": summary["marker_rows"],
            "offset": offset,
            "limit": limit,
            "rows": page,
        }
    except sqlite3.Error as exc:
        logger.error("marker candidates read failed: %s", type(exc).__name__)
        raise HTTPException(409, "marker 状态读取失败") from exc
    except EVIDENCE_ERRORS as exc:
        raise HTTPException(409, "marker 候选或来源身份变化；请检查本地证据") from exc
