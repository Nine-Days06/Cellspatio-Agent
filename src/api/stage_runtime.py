"""四个受控阶段的运行态投影；路由只消费结果，不各自解释 attempt 列表。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

RUNNING = "running"
STAGE_ATTEMPTS = {
    "filter": "attempts",
    "pca": "pca_attempts",
    "cluster": "cluster_attempts",
    "marker": "marker_attempts",
}
ALL_STAGES = tuple(STAGE_ATTEMPTS)


def running_stages(run: dict[str, Any]) -> set[str]:
    """返回 run 快照中当前存在运行中 attempt 的阶段。"""
    return {
        stage
        for stage, key in STAGE_ATTEMPTS.items()
        if any(attempt["status"] == RUNNING for attempt in run.get(key, []))
    }


def has_running(run: dict[str, Any], stages: Iterable[str] = ALL_STAGES) -> bool:
    """指定阶段中是否有阶段正在运行。"""
    return bool(running_stages(run) & set(stages))


def has_other_marker_running(run: dict[str, Any], cluster_attempt_id: str) -> bool:
    """其他 cluster 是否有运行中的 marker attempt。"""
    return any(
        attempt["status"] == RUNNING and attempt["cluster_attempt_id"] != cluster_attempt_id
        for attempt in run.get("marker_attempts", [])
    )
