"""四个受控阶段的运行态投影；路由只消费结果，不各自解释 attempt 列表。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.ui.run_snapshot import RunSnapshot

RUNNING = "running"
ALL_STAGES = ("filter", "pca", "cluster", "marker")


def _snapshot(run: Mapping[str, Any]) -> RunSnapshot:
    """普通 Mapping 包装为快照读模型；已是快照时原样使用。"""
    return run if isinstance(run, RunSnapshot) else RunSnapshot(dict(run))


def running_stages(run: Mapping[str, Any]) -> set[str]:
    """返回 run 快照中当前存在运行中 attempt 的阶段。"""
    snapshot = _snapshot(run)
    return {
        stage
        for stage in ALL_STAGES
        if any(attempt["status"] == RUNNING for attempt in snapshot.stage_attempts(stage))
    }


def has_running(run: Mapping[str, Any], stages: Iterable[str] = ALL_STAGES) -> bool:
    """指定阶段中是否有阶段正在运行。"""
    return bool(running_stages(run) & set(stages))


def has_other_marker_running(run: Mapping[str, Any], cluster_attempt_id: str) -> bool:
    """其他 cluster 是否有运行中的 marker attempt。"""
    return any(
        attempt["status"] == RUNNING and attempt["cluster_attempt_id"] != cluster_attempt_id
        for attempt in _snapshot(run).marker_attempts()
    )
