"""探索性聚类接受的只读身份绑定；不改变科学状态或执行分析。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.analysis.evidence import digest

if TYPE_CHECKING:
    from src.ui.run_snapshot import RunSnapshot

CLUSTER_RECORD_VERSION = "sc-cluster-review/1.0.0"


def review_binding(run: Mapping[str, Any], cluster: dict[str, Any]) -> str:
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
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())


def review_view(run: RunSnapshot, cluster: dict[str, Any], root: Path) -> dict[str, Any]:
    """当前复核需最新 revision；下游执行展示另使用自身授权快照。"""
    # sc_markers 的来源校验依赖本模块，延迟导入避免形成循环。
    from src.analysis.review_facts import cluster_review_facts

    facts = cluster_review_facts(run, cluster, root)
    checked = facts.result
    blocked = []
    if facts.invalidated_reason:
        blocked.append(facts.invalidated_reason)
    if facts.blocked:
        blocked.append("该 run 有阶段运行中，不能记录聚类复核")
    history = run.cluster_reviews(cluster["attempt_id"])
    latest = history[-1] if history else None
    current = (
        {k: v for k, v in facts.current.items() if k != "outcome"}
        if facts.current and facts.current["outcome"] == "accepted"
        else None
    )
    latest_decision = (
        {
            "decision": facts.latest["outcome"],
            **{k: facts.latest[k] for k in ("reason", "created_at", "source_revision")},
            "invalidated_reason": facts.review_invalidated_reason,
        }
        if facts.latest
        else None
    )
    return {
        "run_id": run["run_id"],
        "cluster_attempt_id": cluster["attempt_id"],
        "record_version": CLUSTER_RECORD_VERSION,
        "revision": run["revision"],
        "stage": "cluster_review",
        "scientific_status": "unreviewed",
        "binding_sha256": review_binding(run, cluster) if checked and not blocked else None,
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
        "latest_decision": latest_decision,
    }
