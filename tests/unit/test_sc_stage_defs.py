"""四个受控阶段的产物目录、终态文案与失败投影。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.ui.sc_stage_defs import (
    CLUSTER_STAGE,
    FILTER_STAGE,
    MARKER_STAGE,
    PCA_STAGE,
    STAGE_SPECS,
    stage_directory,
    stage_failure,
)
from src.ui.stage_lifecycle import StageSpec


def row(**values: str) -> sqlite3.Row:
    """构造阶段消息需要的 attempt 行。"""
    columns = ", ".join(f"? AS {column}" for column in values)
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(f"SELECT {columns}", tuple(values.values())).fetchone()


@pytest.mark.parametrize(
    ("spec", "directory_name", "failure_stage", "failure_error"),
    [
        (FILTER_STAGE, "sc_filter", "filter_review", "过滤产物落盘失败"),
        (PCA_STAGE, "sc_pca", "pca_review", "PCA 产物落盘失败"),
        (CLUSTER_STAGE, "sc_clusters", "cluster_review", "聚类产物落盘失败"),
        (MARKER_STAGE, "sc_markers", "marker_review", "marker 产物落盘失败"),
    ],
)
def test_stage_specs_fill_output_directory_and_failure_projection(
    spec: StageSpec, directory_name: str, failure_stage: str, failure_error: str
) -> None:
    """四个阶段各自拥有目录名与固定失败文案。"""
    assert (spec.directory_name, spec.failure_stage, spec.failure_error) == (
        directory_name,
        failure_stage,
        failure_error,
    )


def test_stage_directory_keeps_outputs_beside_run_root() -> None:
    """产物父目录紧邻 run 根目录，attempt 目录只多一层。"""
    root = Path("/analysis/runs/run")
    parent, directory = stage_directory(CLUSTER_STAGE, root, "a1")
    assert parent == root.parent / "sc_clusters"
    assert directory == parent / "a1"


@pytest.mark.parametrize("spec", STAGE_SPECS)
def test_stage_failure_reports_technical_failure_only(spec: StageSpec) -> None:
    """失败结果固定为技术失败，科学状态保持未复核。"""
    assert stage_failure(spec, spec.failure_error) == {
        "status": "failed",
        "stage": spec.failure_stage,
        "scientific_status": "unreviewed",
        "error": spec.failure_error,
    }


@pytest.mark.parametrize(
    ("spec", "attempt", "completed", "failed"),
    [
        (
            FILTER_STAGE,
            {"run_id": "run", "attempt_id": "a1"},
            "过滤产物已保存，等待审阅；科学未复核，未执行归一化或聚类。",
            "过滤失败，本地证据已保留；不会自动重跑。",
        ),
        (
            PCA_STAGE,
            {"run_id": "run", "filter_attempt_id": "f1", "attempt_id": "a1"},
            "归一化与 PCA 产物已保存，等待审阅；科学未复核，未执行聚类或注释。",
            "归一化与 PCA 失败，本地证据保留；不会自动重跑。",
        ),
        (
            CLUSTER_STAGE,
            {"run_id": "run", "pca_attempt_id": "p1", "attempt_id": "a1"},
            "聚类与 UMAP 产物已保存，科学未复核；未执行 markers 或注释。",
            "聚类与 UMAP 失败或中断，本地证据保留；不会自动重跑。",
        ),
        (
            MARKER_STAGE,
            {"run_id": "run", "cluster_attempt_id": "c1", "attempt_id": "a1"},
            "marker 候选与诊断产物已保存，科学未复核；未执行细胞类型注释。",
            "marker 失败或中断，本地证据保留；不会自动重跑。",
        ),
    ],
)
def test_stage_messages_keep_review_prompts(
    spec: StageSpec,
    attempt: dict[str, str],
    completed: str,
    failed: str,
) -> None:
    """完成与失败都保留阶段审阅提示和未复核状态。"""
    attempt_row = row(**attempt)
    content, results = spec.message(attempt_row, {"status": "completed"})
    assert content == completed
    summary = next(iter(results.values()))
    assert summary["stage"] == spec.failure_stage
    assert summary["technical_status"] == "completed"
    assert summary["scientific_status"] == "unreviewed"

    content, results = spec.message(attempt_row, {"status": "failed"})
    assert content == failed
    assert next(iter(results.values()))["technical_status"] == "failed"
