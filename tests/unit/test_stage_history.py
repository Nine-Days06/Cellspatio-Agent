"""阶段历史投影的字段集合与顺序契约。"""

from __future__ import annotations

import pytest

from src.api.stage_history import RESULT_FIELDS, history_entry

COMMON = {
    "attempt_id": "attempt-1",
    "review_id": "review-1",
    "binding_sha256": "sha256",
    "status": "completed",
    "directory": "attempts/attempt-1",
    "created_at": "2026-11-21T00:00:00",
    "finished_at": "2026-11-21T00:01:00",
    "source_revision": 1,
    "claim_revision": 2,
    "preview_id": "preview-1",
}


def _attempt(stage: str) -> dict:
    attempt = dict(COMMON)
    if stage == "cluster":
        attempt["snapshot"] = {"review": {"dims": [1, 2]}}
    return attempt


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (
            "filter",
            [
                "attempt_id",
                "preview_id",
                "status",
                "directory",
                "created_at",
                "finished_at",
                "result",
            ],
        ),
        (
            "pca",
            ["attempt_id", "binding_sha256", "status", "created_at", "finished_at", "result"],
        ),
        (
            "cluster",
            [
                "attempt_id",
                "review_id",
                "binding_sha256",
                "status",
                "created_at",
                "finished_at",
                "source_revision",
                "claim_revision",
                "dims",
                "invalidated_reason",
                "result",
                "error",
            ],
        ),
        (
            "marker",
            [
                "attempt_id",
                "review_id",
                "binding_sha256",
                "status",
                "created_at",
                "finished_at",
                "source_revision",
                "claim_revision",
                "invalidated_reason",
                "result",
                "error",
                "review",
            ],
        ),
    ],
)
def test_history_entry_fields(stage: str, expected: list[str]) -> None:
    """四阶段响应键与顺序固定，且 filter/pca 不多出 null 扩展键。"""
    result = {key: f"{key}-value" for key in RESULT_FIELDS[stage]}
    result.update(cells=4, versions=["v1"], not_exported="secret")
    entry = history_entry(
        stage,
        _attempt(stage),
        result=result,
        invalidated_reason="已失效",
        error="执行失败",
        review={"decision": "accepted"},
    )

    assert list(entry) == expected
    assert entry["result"] == {
        key: result[key]
        for key in RESULT_FIELDS[stage]
        if stage in ("cluster", "marker") or key in result
    }


@pytest.mark.parametrize("stage", ["cluster", "marker"])
def test_history_entry_keeps_null_extension_keys(stage: str) -> None:
    """cluster/marker 既有契约要求扩展键始终存在并可为空。"""
    entry = history_entry(stage, _attempt(stage))

    assert entry["invalidated_reason"] is None
    assert entry["error"] is None
    assert entry["result"] is None
    if stage == "marker":
        assert entry["review"] is None


def test_filter_and_pca_result_preserve_source_order() -> None:
    """上游结果投影不重排键，保持既有 JSON 顺序。"""
    source = {"scientific_status": "unreviewed", "cells": 4, "features": 8}

    assert list(history_entry("filter", _attempt("filter"), result=source)["result"]) == [
        "scientific_status",
        "cells",
        "features",
    ]
    assert list(history_entry("pca", _attempt("pca"), result=source)["result"]) == [
        "scientific_status",
        "cells",
        "features",
    ]


@pytest.mark.parametrize("stage", ["cluster", "marker"])
def test_review_stage_result_requires_all_fields(stage: str) -> None:
    """cluster/marker 仍按既有逻辑取齐 allowlist，缺字段时暴露上游异常。"""
    with pytest.raises(KeyError):
        history_entry(stage, _attempt(stage), result={"cells": 4})


def test_failed_review_stage_uses_existing_error_text() -> None:
    """失败文案由投影统一提供，调用方无需重复。"""
    attempt = _attempt("cluster")
    attempt["status"] = "failed"

    assert history_entry("cluster", attempt)["error"] == "执行失败，本地证据保留；不会自动重跑"
