"""共享阶段运行态投影。"""

import pytest

from src.api.stage_runtime import (
    ALL_STAGES,
    RUNNING,
    STAGE_ATTEMPTS,
    has_other_marker_running,
    has_running,
    running_stages,
)


@pytest.mark.parametrize(("stage", "key"), STAGE_ATTEMPTS.items())
def test_each_stage_running(stage: str, key: str) -> None:
    """每个阶段的 attempt 列表单独映射。"""
    run = {key: [{"status": RUNNING}]}
    assert running_stages(run) == {stage}
    assert has_running(run, (stage,))


def test_empty_run_has_nothing_running() -> None:
    """空 run 或缺失 attempt 列表视为无运行阶段。"""
    assert running_stages({}) == set()
    assert not has_running({})


def test_multiple_stages_running() -> None:
    """多个阶段可同时处于运行态。"""
    run = {
        "attempts": [{"status": RUNNING}],
        "cluster_attempts": [{"status": "completed"}, {"status": RUNNING}],
    }
    assert running_stages(run) == {"filter", "cluster"}
    assert has_running(run)
    assert has_running(run, ALL_STAGES)
    assert not has_running(run, ("pca", "marker"))


def test_marker_across_clusters() -> None:
    """marker 运行态按 cluster 区分。"""
    run = {
        "marker_attempts": [
            {"status": RUNNING, "cluster_attempt_id": "a"},
            {"status": "completed", "cluster_attempt_id": "b"},
        ]
    }
    assert has_other_marker_running(run, "b")
    assert not has_other_marker_running(run, "a")
