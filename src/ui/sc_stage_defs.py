"""四个受控阶段的阶段描述；运行态、互斥与中断恢复共用这一份注册表。"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.ui.stage_lifecycle import StageSpec


def _filter_message(attempt: sqlite3.Row, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """过滤终态的固定文案与安全汇总。"""
    content = (
        "过滤产物已保存，等待审阅；科学未复核，未执行归一化或聚类。"
        if result["status"] == "completed"
        else "过滤失败，本地证据已保留；不会自动重跑。"
    )
    return content, {
        "sc_filter": {
            "run_id": attempt["run_id"],
            "attempt_id": attempt["attempt_id"],
            "stage": "filter_review",
            "technical_status": result["status"],
            "scientific_status": "unreviewed",
        }
    }


FILTER_STAGE = StageSpec(
    table="sc_filter_attempts",
    match_columns=("preview_id", "binding_sha256", "tool_version"),
    terminal=("completed", "failed"),
    missing_run="QC 关联不存在",
    revision_error="仅可执行当前已批准的精确选择，请刷新",
    invalid_terminal="过滤终态非法",
    not_running="过滤任务不在运行状态",
    message=_filter_message,
    label="过滤",
)


def _pca_message(attempt: sqlite3.Row, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """PCA 终态的固定文案与安全汇总。"""
    content = (
        "归一化与 PCA 产物已保存，等待审阅；科学未复核，未执行聚类或注释。"
        if result["status"] == "completed"
        else "归一化与 PCA 失败，本地证据保留；不会自动重跑。"
    )
    return content, {
        "sc_pca": {
            "run_id": attempt["run_id"],
            "filter_attempt_id": attempt["filter_attempt_id"],
            "attempt_id": attempt["attempt_id"],
            "stage": "pca_review",
            "technical_status": result["status"],
            "scientific_status": "unreviewed",
        }
    }


PCA_STAGE = StageSpec(
    table="sc_pca_attempts",
    match_columns=("filter_attempt_id", "binding_sha256", "tool_version"),
    terminal=("completed", "failed"),
    missing_run="QC 关联不存在",
    revision_error="仅可授权当前已批准且过滤完成的父阶段，请刷新",
    invalid_terminal="PCA 终态非法",
    not_running="PCA 任务不在运行状态",
    message=_pca_message,
    label="PCA",
)


def _cluster_message(attempt: sqlite3.Row, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """聚类终态的固定文案与安全汇总。"""
    content = (
        "聚类与 UMAP 产物已保存，科学未复核；未执行 markers 或注释。"
        if result["status"] == "completed"
        else "聚类与 UMAP 失败或中断，本地证据保留；不会自动重跑。"
    )
    return content, {
        "sc_cluster": {
            "run_id": attempt["run_id"],
            "pca_attempt_id": attempt["pca_attempt_id"],
            "attempt_id": attempt["attempt_id"],
            "stage": "cluster_review",
            "technical_status": result["status"],
            "scientific_status": "unreviewed",
        }
    }


CLUSTER_STAGE = StageSpec(
    table="sc_cluster_attempts",
    match_columns=("pca_attempt_id", "binding_sha256", "tool_version"),
    terminal=("completed", "failed", "interrupted"),
    missing_run="QC 关联不存在",
    revision_error="维度或父身份已变化，请刷新后重新授权",
    invalid_terminal="聚类终态非法",
    not_running="聚类任务不在运行状态",
    message=_cluster_message,
    label="聚类",
    interrupt_result={
        "status": "interrupted",
        "stage": "cluster_review",
        "scientific_status": "unreviewed",
    },
)


def _marker_message(attempt: sqlite3.Row, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """marker 终态的固定文案与安全汇总。"""
    content = (
        "marker 候选与诊断产物已保存，科学未复核；未执行细胞类型注释。"
        if result["status"] == "completed"
        else "marker 失败或中断，本地证据保留；不会自动重跑。"
    )
    return content, {
        "sc_markers": {
            "run_id": attempt["run_id"],
            "cluster_attempt_id": attempt["cluster_attempt_id"],
            "attempt_id": attempt["attempt_id"],
            "stage": "marker_review",
            "technical_status": result["status"],
            "scientific_status": "unreviewed",
        }
    }


MARKER_STAGE = StageSpec(
    table="sc_marker_attempts",
    match_columns=("cluster_attempt_id", "binding_sha256", "tool_version"),
    terminal=("completed", "failed", "interrupted"),
    missing_run="QC 关联不存在",
    revision_error="聚类接受或父身份已变化，请刷新后重新授权",
    invalid_terminal="marker 终态非法",
    not_running="marker 任务不在运行状态",
    message=_marker_message,
    label="marker",
    interrupt_result={
        "status": "interrupted",
        "stage": "marker_review",
        "scientific_status": "unreviewed",
    },
)


STAGE_SPECS = (FILTER_STAGE, PCA_STAGE, CLUSTER_STAGE, MARKER_STAGE)
