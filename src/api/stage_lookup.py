"""跨阶段路由共用的只读定位；路径与父链不能来自客户端。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from src.ui.analysis_roots import AnalysisRoots
from src.ui.run_snapshot import RunSnapshot
from src.ui.session_store import SessionStore


def run_qc_root(roots: AnalysisRoots, run: RunSnapshot) -> Path:
    """由应用注入的 roots 校验并返回 run 的 QC 根。"""
    return roots.qc_root(run)


def require_run(store: SessionStore, session_id: str, run_id: str) -> RunSnapshot:
    """关联缺失/跨会话统一 404，不扫描旧消息路径。"""
    run = store.get_qc_run(session_id, run_id)
    if run is None:
        raise HTTPException(404, "QC 关联不存在；旧证据需重新 QC 才能交互")
    return run


def require_parent(run: RunSnapshot, filter_attempt_id: str) -> dict[str, Any]:
    """仅定位已关联父记录。"""
    parent = run.attempt("filter", filter_attempt_id)
    if parent is None:
        raise HTTPException(404, "父过滤关联不存在")
    return parent


def require_pca(run: RunSnapshot, filter_attempt_id: str, attempt_id: str) -> dict[str, Any]:
    """只定位本会话关联的 PCA。"""
    attempt = run.attempt("pca", attempt_id, filter_attempt_id)
    if attempt is None:
        raise HTTPException(404, "PCA 关联不存在")
    return attempt


def require_cluster(run: RunSnapshot, pca_attempt_id: str, attempt_id: str) -> dict[str, Any]:
    """只定位本 run 与 PCA 下的确切聚类尝试。"""
    cluster = run.attempt("cluster", attempt_id, pca_attempt_id)
    if cluster is None:
        raise HTTPException(404, "聚类关联不存在")
    return cluster
