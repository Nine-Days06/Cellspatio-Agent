"""探索性聚类接受的只读身份绑定；不改变科学状态或执行分析。"""

import json
from pathlib import Path
from typing import Any

from src.analysis.qc_preview import _digest
from src.analysis.sc_cluster import checked_completed_cluster
from src.analysis.sc_pca import DIMENSION_RECORD_VERSION

CLUSTER_RECORD_VERSION = "sc-cluster-review/1.0.0"


def review_binding(run: dict[str, Any], cluster: dict[str, Any]) -> str:
    """绑定完整已校验结果，不包含自由文本依据或当前 revision。"""
    payload = {
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "cluster": {
            k: cluster[k]
            for k in (
                "attempt_id",
                "pca_attempt_id",
                "binding_sha256",
                "tool_version",
                "protocol",
                "result",
            )
        },
        "record_version": CLUSTER_RECORD_VERSION,
    }
    return _digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())


def review_view(run: dict[str, Any], cluster: dict[str, Any], root: Path) -> dict[str, Any]:
    """当前复核需最新 revision；下游执行展示另使用自身授权快照。"""
    checked, binding, blocked = None, None, []
    try:
        checked = checked_completed_cluster(run, cluster, root, DIMENSION_RECORD_VERSION)
        binding = review_binding(run, cluster)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, StopIteration, IndexError):
        blocked.append("聚类来源、协议或本地产物不符；仅保留历史，不能记录接受")
    if any(
        a["status"] == "running"
        for key in (
            "attempts",
            "pca_attempts",
            "cluster_attempts",
            "marker_attempts",
        )
        for a in run.get(key, [])
    ):
        blocked.append("该 run 有阶段运行中，不能记录聚类复核")
    history = [
        d for d in run["cluster_reviews"] if d["cluster_attempt_id"] == cluster["attempt_id"]
    ]
    latest = history[-1] if history else None
    current = (
        latest
        if (
            not blocked
            and latest
            and latest["accepted"] is True
            and latest["binding_sha256"] == binding
            and latest["record_version"] == CLUSTER_RECORD_VERSION
            and latest["source_revision"] + 1 == run["revision"]
        )
        else None
    )
    return {
        "run_id": run["run_id"],
        "cluster_attempt_id": cluster["attempt_id"],
        "record_version": CLUSTER_RECORD_VERSION,
        "revision": run["revision"],
        "stage": "cluster_review",
        "scientific_status": "unreviewed",
        "binding_sha256": binding if not blocked else None,
        "evidence": {k: checked[k] for k in ("cells", "clusters", "cluster_sizes", "versions")}
        if checked
        else None,
        "blocked": blocked,
        "current": current,
        "history": [
            {
                **d,
                "invalidated_reason": None
                if d == current
                else (
                    "已有更新的人工复核"
                    if d != latest
                    else "来源、记录版本或 revision 已变化；首次 marker 授权须重新明确记录"
                ),
            }
            for d in history
        ],
    }
