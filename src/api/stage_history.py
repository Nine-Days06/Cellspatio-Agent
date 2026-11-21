"""阶段 attempt 历史的统一投影，保持四个视图的响应键与顺序。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

HISTORY_FIELDS: dict[str, tuple[str, ...]] = {
    "filter": ("attempt_id", "preview_id", "status", "directory", "created_at", "finished_at"),
    "pca": ("attempt_id", "binding_sha256", "status", "created_at", "finished_at"),
    "cluster": (
        "attempt_id",
        "review_id",
        "binding_sha256",
        "status",
        "created_at",
        "finished_at",
        "source_revision",
        "claim_revision",
    ),
    "marker": (
        "attempt_id",
        "review_id",
        "binding_sha256",
        "status",
        "created_at",
        "finished_at",
        "source_revision",
        "claim_revision",
    ),
}

RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    "filter": ("cells", "features", "error", "versions", "artifacts", "scientific_status"),
    "pca": (
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
    ),
    "cluster": (
        "cells",
        "clusters",
        "cluster_sizes",
        "n_epochs",
        "versions",
        "seurat_threads",
    ),
    "marker": (
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
    ),
}

FAILED_ERROR = "执行失败，本地证据保留；不会自动重跑"


def _result(stage: str, result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """按阶段 allowlist 投影结果；filter/pca 保留来源顺序，review 阶段按表取键。"""
    if not result:
        return None
    fields = RESULT_FIELDS[stage]
    if stage in ("filter", "pca"):
        return {key: value for key, value in result.items() if key in fields}
    return {key: result[key] for key in fields}


def history_entry(
    stage: str,
    attempt: Mapping[str, Any],
    *,
    result: Mapping[str, Any] | None = None,
    invalidated_reason: str | None = None,
    error: str | None = None,
    review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """生成阶段历史项；cluster/marker 保留既有 null 键，filter/pca 只含结果。"""
    entry = {key: attempt[key] for key in HISTORY_FIELDS[stage]}
    if stage == "cluster":
        entry["dims"] = attempt["snapshot"]["review"]["dims"]
    if stage in ("cluster", "marker"):
        entry["invalidated_reason"] = invalidated_reason
    entry["result"] = _result(stage, result)
    if stage in ("cluster", "marker"):
        entry["error"] = (
            error if error is not None else FAILED_ERROR if attempt["status"] == "failed" else None
        )
    if stage == "marker":
        entry["review"] = review
    return entry
