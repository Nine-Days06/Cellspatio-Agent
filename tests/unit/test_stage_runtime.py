"""共享阶段运行态投影。"""

import ast
from pathlib import Path

import pytest

from src.api.stage_runtime import (
    ALL_STAGES,
    RUNNING,
    has_other_marker_running,
    has_running,
    running_stages,
)
from src.ui.run_snapshot import RunSnapshot

_STAGE_KEYS = {
    "filter": "attempts",
    "pca": "pca_attempts",
    "cluster": "cluster_attempts",
    "marker": "marker_attempts",
}


@pytest.mark.parametrize("stage", ALL_STAGES)
def test_each_stage_running(stage: str) -> None:
    """每个阶段的 attempt 列表单独映射。"""
    run = {_STAGE_KEYS[stage]: [{"status": RUNNING}]}
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


def test_snapshot_input_reads_through_stage_accessors() -> None:
    """RunSnapshot 输入按阶段访问器读取，不依赖调用方拆裸键。"""
    run = RunSnapshot(
        {
            "attempts": [{"status": RUNNING}],
            "marker_attempts": [
                {"status": RUNNING, "cluster_attempt_id": "a"},
                {"status": "completed", "cluster_attempt_id": "b"},
            ],
        }
    )
    assert running_stages(run) == {"filter", "marker"}
    assert has_other_marker_running(run, "b")
    assert not has_other_marker_running(run, "a")


def _string_literals(relative_path: str) -> set[str]:
    """读取模块源码中的全部字符串字面量。"""
    path = Path(__file__).resolve().parents[2] / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


@pytest.mark.parametrize("relative_path", ("src/api/stage_runtime.py", "src/ui/record_files.py"))
def test_modules_do_not_read_bare_stage_keys(relative_path: str) -> None:
    """运行态与产物枚举只经 RunSnapshot 访问器读取阶段表。"""
    assert _string_literals(relative_path) & set(_STAGE_KEYS.values()) == set()
