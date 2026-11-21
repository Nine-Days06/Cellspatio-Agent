"""RunSnapshot 读模型：访问器、排序、过滤与 JSON 解码。"""

import json

import pytest

from src.cli.records import _json_safe
from src.ui.record_files import run_dirs
from src.ui.run_snapshot import RunSnapshot
from src.ui.session_store import SessionStore


def _raw_run() -> dict:
    return {
        "run_id": "run-1",
        "session_id": "sid",
        "revision": 3,
        "evidence": {"summary": {"cells": 10}},
        "active_preview_id": "p2",
        "previews": [
            {"preview_id": "p1", "status": "approved"},
            {"preview_id": "p2", "status": "pending"},
        ],
        "attempts": [
            {"attempt_id": "fz", "preview_id": "p1"},
            {"attempt_id": "fa", "preview_id": "p2"},
        ],
        "pca_attempts": [
            {"attempt_id": "pz", "filter_attempt_id": "fz"},
            {"attempt_id": "pa", "filter_attempt_id": "fa"},
        ],
        "dimension_reviews": [
            {"review_id": "dz", "pca_attempt_id": "pz"},
            {"review_id": "da", "pca_attempt_id": "pa"},
        ],
        "cluster_attempts": [
            {"attempt_id": "cz", "pca_attempt_id": "pz"},
            {"attempt_id": "ca", "pca_attempt_id": "pa"},
        ],
        "cluster_reviews": [
            {"review_id": "rz", "cluster_attempt_id": "cz"},
            {"review_id": "ra", "cluster_attempt_id": "ca"},
        ],
        "cluster_rejections": [
            {"review_id": "jz", "cluster_attempt_id": "cz"},
        ],
        "marker_attempts": [
            {"attempt_id": "mz", "cluster_attempt_id": "cz"},
            {"attempt_id": "ma", "cluster_attempt_id": "ca"},
        ],
        "marker_reviews": [
            {"review_id": "nz", "marker_attempt_id": "mz"},
        ],
    }


def test_empty_run_accessors() -> None:
    """空 run 的列表访问器返回空、active_preview 为 None。"""
    run = RunSnapshot({"run_id": "empty", "evidence": {}, "revision": 0})
    assert run.previews == []
    assert run.active_preview is None
    assert run.filter_attempts == []
    assert run.pca_attempts() == []
    assert run.dimension_reviews() == []
    assert run.cluster_attempts() == []
    assert run.cluster_reviews() == []
    assert run.cluster_rejections() == []
    assert run.marker_attempts() == []
    assert run.marker_reviews() == []


def test_mapping_delegates_to_raw() -> None:
    """既有领域代码仍可按 Mapping 读取原始键。"""
    run = RunSnapshot(_raw_run())
    assert run["run_id"] == "run-1"
    assert run.get("missing", "fallback") == "fallback"
    assert run["attempts"][0]["attempt_id"] == "fz"
    assert dict(run.items())["revision"] == 3
    assert list(run) == list(_raw_run())


def test_accessors_preserve_order_and_filter_by_parent() -> None:
    """访问器保持 rowid 顺序，并按父 ID 过滤。"""
    run = RunSnapshot(_raw_run())
    assert [a["attempt_id"] for a in run.filter_attempts] == ["fz", "fa"]
    assert [a["attempt_id"] for a in run.pca_attempts()] == ["pz", "pa"]
    assert [a["attempt_id"] for a in run.pca_attempts("fa")] == ["pa"]
    assert [d["review_id"] for d in run.dimension_reviews("pz")] == ["dz"]
    assert [a["attempt_id"] for a in run.cluster_attempts("pa")] == ["ca"]
    assert [d["review_id"] for d in run.cluster_reviews("cz")] == ["rz"]
    assert [d["review_id"] for d in run.cluster_rejections("cz")] == ["jz"]
    assert [a["attempt_id"] for a in run.marker_attempts("ca")] == ["ma"]
    assert [d["review_id"] for d in run.marker_reviews("mz")] == ["nz"]


def test_stage_attempts_covers_all_stages_and_unknown_raises() -> None:
    """阶段访问器返回四个阶段的列表副本，未知阶段拒绝。"""
    run = RunSnapshot(_raw_run())
    assert [a["attempt_id"] for a in run.stage_attempts("filter")] == ["fz", "fa"]
    assert [a["attempt_id"] for a in run.stage_attempts("pca")] == ["pz", "pa"]
    assert [a["attempt_id"] for a in run.stage_attempts("cluster")] == ["cz", "ca"]
    assert [a["attempt_id"] for a in run.stage_attempts("marker")] == ["mz", "ma"]

    copies = run.stage_attempts("filter")
    copies.clear()
    assert [a["attempt_id"] for a in run.stage_attempts("filter")] == ["fz", "fa"]

    empty = RunSnapshot({"run_id": "empty"})
    assert empty.stage_attempts("filter") == []
    assert empty.stage_attempts("pca") == []
    assert empty.stage_attempts("cluster") == []
    assert empty.stage_attempts("marker") == []

    with pytest.raises(ValueError):
        run.stage_attempts("unknown")


def test_attempt_locates_by_id_and_optional_parent() -> None:
    """attempt 返回首个精确匹配，并按阶段父列校验父 ID。"""
    run = RunSnapshot(_raw_run())
    filter_attempt = run.attempt("filter", "fa")
    assert filter_attempt is not None and filter_attempt["preview_id"] == "p2"
    pca_attempt = run.attempt("pca", "pa")
    assert pca_attempt is not None and pca_attempt["filter_attempt_id"] == "fa"
    assert run.attempt("pca", "pa", "fa") is pca_attempt
    assert run.attempt("cluster", "cz", "fa") is None
    marker_attempt = run.attempt("marker", "ma", "ca")
    assert marker_attempt is not None and marker_attempt["attempt_id"] == "ma"
    assert run.attempt("marker", "ma", "cz") is None
    assert run.attempt("pca", "missing") is None
    assert RunSnapshot({"run_id": "empty"}).attempt("pca", "pa") is None

    duplicated = RunSnapshot(
        {
            "pca_attempts": [
                {"attempt_id": "pa", "filter_attempt_id": "f1"},
                {"attempt_id": "pa", "filter_attempt_id": "f2"},
            ]
        }
    )
    assert duplicated.attempt("pca", "pa")["filter_attempt_id"] == "f1"

    with pytest.raises(ValueError):
        run.attempt("unknown", "fa")
    with pytest.raises(ValueError):
        run.attempt("unknown", "fa", "fz")
    with pytest.raises(ValueError):
        run.attempt("filter", "fa", "fa")


def test_attempt_parent_column_is_strict() -> None:
    """损坏的子行缺少父列时不能伪装成未找到。"""
    run = RunSnapshot({"pca_attempts": [{"attempt_id": "pa"}]})
    with pytest.raises(KeyError):
        run.attempt("pca", "pa", "fa")


def test_coerce_wraps_mapping_and_keeps_snapshot_identity() -> None:
    """普通 Mapping 统一包装，已是快照时原样返回。"""
    raw = {"run_id": "r1", "attempts": [{"attempt_id": "f1"}]}
    wrapped = RunSnapshot.coerce(raw)
    assert isinstance(wrapped, RunSnapshot)
    assert wrapped["run_id"] == "r1"
    assert wrapped.stage_attempts("filter")[0]["attempt_id"] == "f1"
    assert RunSnapshot.coerce(wrapped) is wrapped


def test_stage_dirs_follow_run_dirs_order_and_skip_empty() -> None:
    """stage_dirs 返回 QC 与四阶段非空目录，顺序与 run_dirs 一致。"""
    raw = {
        "run_id": "dirs",
        "directory": "runs/qc",
        "attempts": [
            {"attempt_id": "f1", "directory": "runs/f1"},
            {"attempt_id": "f2", "directory": ""},
        ],
        "pca_attempts": [{"attempt_id": "p1", "directory": "runs/p1"}],
        "cluster_attempts": [{"attempt_id": "c1", "directory": "runs/c1"}],
        "marker_attempts": [
            {"attempt_id": "m1"},
            {"attempt_id": "m2", "directory": "runs/m2"},
        ],
    }
    run = RunSnapshot(raw)
    assert run.stage_dirs() == [
        ("qc", "runs/qc"),
        ("filter", "runs/f1"),
        ("pca", "runs/p1"),
        ("cluster", "runs/c1"),
        ("marker", "runs/m2"),
    ]
    assert RunSnapshot({"run_id": "empty"}).stage_dirs() == []


def test_active_preview_and_previews() -> None:
    """active_preview 按 active_preview_id 定位，缺失时返回 None。"""
    run = RunSnapshot(_raw_run())
    assert [p["preview_id"] for p in run.previews] == ["p1", "p2"]
    assert run.active_preview["preview_id"] == "p2"
    assert RunSnapshot({"active_preview_id": "nope", "previews": []}).active_preview is None


def test_store_decodes_json_and_returns_snapshot(tmp_path) -> None:
    """get_qc_run 返回 RunSnapshot，并只在 store 内解码 JSON。"""
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session()
    rid = "run-json"
    store.register_qc_run(sid, rid, str(tmp_path / "qc"), {"summary": {"cells": 12}})
    store.activate_qc_preview(
        sid,
        rid,
        0,
        {"preview_id": "p1", "summary": {"blocked": False}, "nested": {"ok": True}},
    )
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO sc_filter_attempts "
            "(attempt_id, run_id, preview_id, binding_sha256, tool_version, directory, "
            "status, created_at, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "fz",
                rid,
                "p1",
                "b" * 64,
                "filter/1",
                str(tmp_path / "fz"),
                "completed",
                "2026-01-02",
                json.dumps({"cells": 8}),
            ),
        )
        conn.execute(
            "INSERT INTO sc_filter_attempts "
            "(attempt_id, run_id, preview_id, binding_sha256, tool_version, directory, "
            "status, created_at, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "fa",
                rid,
                "p1",
                "a" * 64,
                "filter/1",
                str(tmp_path / "fa"),
                "completed",
                "2026-01-01",
                None,
            ),
        )
        conn.execute(
            "INSERT INTO sc_pca_attempts "
            "(attempt_id, filter_attempt_id, run_id, binding_sha256, tool_version, protocol, "
            "reason, directory, status, created_at, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "pa",
                "fz",
                rid,
                "c" * 64,
                "pca/1",
                "pca/1",
                "test",
                str(tmp_path / "pa"),
                "completed",
                "2026-01-03",
                json.dumps({"actual_pcs": 2}),
            ),
        )
        conn.execute(
            "INSERT INTO sc_pca_dimension_reviews "
            "(review_id, pca_attempt_id, binding_sha256, record_version, source_revision, "
            "dims, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "d1",
                "pa",
                "c" * 64,
                "sc-pca-dimensions/1.0.0",
                1,
                json.dumps([1, 2]),
                "test",
                "2026-01-04",
            ),
        )
        conn.execute(
            "INSERT INTO sc_cluster_attempts "
            "(attempt_id, run_id, pca_attempt_id, review_id, snapshot, binding_sha256, "
            "tool_version, protocol, source_revision, claim_revision, reason, directory, "
            "status, created_at, result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ca",
                rid,
                "pa",
                "d1",
                json.dumps({"clusters": 4}),
                "e" * 64,
                "cluster/1",
                "cluster/1",
                1,
                1,
                "test",
                str(tmp_path / "ca"),
                "completed",
                "2026-01-05",
                None,
            ),
        )
        conn.execute(
            "INSERT INTO sc_cluster_reviews "
            "(review_id, cluster_attempt_id, binding_sha256, record_version, source_revision, "
            "accepted, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cr1",
                "ca",
                "e" * 64,
                "sc-cluster/1.0.0",
                1,
                1,
                "test",
                "2026-01-06",
            ),
        )

    run = store.get_qc_run(sid, rid)
    assert isinstance(run, RunSnapshot)
    assert run["evidence"] == {"summary": {"cells": 12}}
    assert run.active_preview["preview_id"] == "p1"
    assert run.active_preview["summary"] == {"blocked": False}
    assert run.active_preview["nested"] == {"ok": True}
    assert [a["attempt_id"] for a in run.filter_attempts] == ["fz", "fa"]
    assert run.filter_attempts[0]["result"] == {"cells": 8}
    assert run.filter_attempts[1]["result"] is None
    assert run.pca_attempts("fz")[0]["result"] == {"actual_pcs": 2}
    assert run.dimension_reviews("pa")[0]["dims"] == [1, 2]
    assert run.cluster_attempts("pa")[0]["snapshot"] == {"clusters": 4}
    assert run.cluster_reviews("ca")[0]["accepted"] is True
    assert run.pca_attempts("missing") == []
    assert store.get_qc_run(sid, "missing") is None


def test_records_json_safe_handles_snapshot() -> None:
    """records 序列化把 RunSnapshot 当 Mapping，而不是字符串化。"""
    assert _json_safe(RunSnapshot({"run_id": "r1", "evidence": {"cells": 3}}), False) == {
        "run_id": "r1",
        "evidence": {"cells": 3},
    }


def test_run_dirs_delegates_to_stage_dirs_for_snapshot_and_mapping() -> None:
    """run_dirs 与 stage_dirs 同序，普通 Mapping 输入同样可用。"""
    raw = {
        "directory": "runs/qc",
        "attempts": [{"directory": "runs/f"}],
        "pca_attempts": [{"directory": "runs/p"}],
        "cluster_attempts": [{"directory": ""}],
        "marker_attempts": [{"directory": "runs/m"}],
    }
    expected = [
        ("qc", "runs/qc"),
        ("filter", "runs/f"),
        ("pca", "runs/p"),
        ("marker", "runs/m"),
    ]
    assert run_dirs(RunSnapshot(raw)) == expected
    assert run_dirs(raw) == expected
