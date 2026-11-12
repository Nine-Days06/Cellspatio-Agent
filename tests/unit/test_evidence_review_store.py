"""票据 16 补齐：QC/聚类拒绝与 marker 复核事实落库。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.sc_marker_reviews import CLUSTER_RECORD_VERSION
from src.analysis.sc_markers import MARKER_REVIEW_VERSION
from src.cli import evidence
from src.ui.session_store import SessionStore

NOW = "2026-11-12T00:00:00+00:00"


def _seed(store: SessionStore, root: Path) -> None:
    """最小六片链路：session → QC → filter → PCA → cluster → marker。"""
    payload = {
        "binding_sha256": "a" * 64,
        "summary": {"blocked": []},
        "params": {},
        "schema": "qc-thresholds/1",
        "tool_version": "qc-preview/1",
    }
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
            ("s", NOW, NOW),
        )
        conn.execute(
            "INSERT INTO qc_runs (run_id, session_id, directory, evidence, revision) "
            "VALUES (?, ?, ?, ?, ?)",
            ("r", "s", str(root / "sc_qc"), "{}", 1),
        )
        conn.execute(
            "INSERT INTO qc_previews (preview_id, run_id, payload) VALUES (?, ?, ?)",
            ("p1", "r", json.dumps(payload)),
        )
        conn.execute("UPDATE qc_runs SET active_preview_id = 'p1' WHERE run_id = 'r'")
        conn.execute(
            "INSERT INTO sc_filter_attempts "
            "(attempt_id, run_id, preview_id, binding_sha256, tool_version, directory, status, "
            "created_at, result) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, '{}')",
            ("f1", "r", "p1", "a" * 64, "1", str(root / "sc_filter" / "f1"), NOW),
        )
        conn.execute(
            "INSERT INTO sc_pca_attempts "
            "(attempt_id, filter_attempt_id, run_id, binding_sha256, tool_version, protocol, "
            "reason, directory, status, created_at, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, '{}')",
            ("pca1", "f1", "r", "b" * 64, "1", "{}", "ok", str(root / "sc_pca" / "pca1"), NOW),
        )
        conn.execute(
            "INSERT INTO sc_pca_dimension_reviews "
            "(review_id, pca_attempt_id, binding_sha256, record_version, source_revision, dims, "
            "reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "d1",
                "pca1",
                "c" * 64,
                "sc-pca-dimensions/1.0.0",
                0,
                json.dumps([1, 2]),
                "ok",
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO sc_cluster_attempts "
            "(attempt_id, run_id, pca_attempt_id, review_id, snapshot, binding_sha256, "
            "tool_version, protocol, source_revision, claim_revision, reason, directory, status, "
            "created_at, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, '{}')",
            (
                "c1",
                "r",
                "pca1",
                "d1",
                "{}",
                "d" * 64,
                "1",
                "{}",
                0,
                0,
                "ok",
                str(root / "sc_clusters" / "c1"),
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO sc_cluster_reviews "
            "(review_id, cluster_attempt_id, binding_sha256, record_version, source_revision, "
            "accepted, reason, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            ("cr1", "c1", "d" * 64, CLUSTER_RECORD_VERSION, 0, "ok", NOW),
        )
        conn.execute(
            "INSERT INTO sc_marker_attempts "
            "(attempt_id, run_id, cluster_attempt_id, review_id, snapshot, binding_sha256, "
            "tool_version, protocol, source_revision, claim_revision, reason, directory, status, "
            "created_at, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, '{}')",
            (
                "m1",
                "r",
                "c1",
                "cr1",
                "{}",
                "e" * 64,
                "1.0.1",
                "{}",
                0,
                0,
                "ok",
                str(root / "sc_markers" / "m1"),
                NOW,
            ),
        )


def test_record_qc_rejection_and_idempotency(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    _seed(store, tmp_path)

    run = store.record_qc_rejection("s", "r", "p1", "阈值过宽", "reviewer-a")
    preview = run["previews"][0]
    assert preview["status"] == "rejected"
    assert preview["reason"] == "阈值过宽"
    assert preview["reviewer"] == "reviewer-a"
    assert preview["rejected_at"]
    assert run["revision"] == 2

    again = store.record_qc_rejection("s", "r", "p1", "阈值过宽", "reviewer-a")
    assert again["revision"] == 2
    with pytest.raises(ValueError, match="冲突"):
        store.record_qc_rejection("s", "r", "p1", "另一个原因", "reviewer-a")
    with pytest.raises(ValueError, match="已被拒绝"):
        store.approve_qc_preview("s", "r", again["revision"], "p1", "重新批准")


def test_record_cluster_rejection_and_idempotency(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    _seed(store, tmp_path)
    run = store.get_qc_run("s", "r")
    cluster = run["cluster_attempts"][0]

    store.record_sc_cluster_rejection(
        "s",
        run,
        cluster,
        cluster["binding_sha256"],
        CLUSTER_RECORD_VERSION,
        run["revision"],
        "簇结构不稳定",
        "reviewer-a",
    )
    saved = store.get_qc_run("s", "r")
    assert saved["cluster_rejections"][0]["reason"] == "簇结构不稳定"
    assert saved["cluster_rejections"][0]["reviewer"] == "reviewer-a"
    assert saved["revision"] == run["revision"] + 1

    store.record_sc_cluster_rejection(
        "s",
        saved,
        cluster,
        cluster["binding_sha256"],
        CLUSTER_RECORD_VERSION,
        saved["revision"],
        "簇结构不稳定",
        "reviewer-a",
    )
    assert len(store.get_qc_run("s", "r")["cluster_rejections"]) == 1
    with pytest.raises(ValueError, match="已失效"):
        store.record_sc_cluster_rejection(
            "s",
            saved,
            cluster,
            cluster["binding_sha256"],
            CLUSTER_RECORD_VERSION,
            run["revision"],
            "簇结构不稳定",
            "reviewer-a",
        )


def test_record_marker_review_accepted_then_rejected(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    _seed(store, tmp_path)

    run = store.record_sc_marker_review(
        "s",
        "m1",
        "accepted",
        "候选与已知 marker 一致",
        "reviewer-a",
        MARKER_REVIEW_VERSION,
        revision=1,
    )
    assert run["marker_reviews"][0]["decision"] == "accepted"

    updated = store.record_sc_marker_review(
        "s", "m1", "rejected", "候选不足", "reviewer-b", MARKER_REVIEW_VERSION, revision=2
    )
    assert [item["decision"] for item in updated["marker_reviews"]] == ["accepted", "rejected"]
    again = store.record_sc_marker_review(
        "s", "m1", "rejected", "候选不足", "reviewer-b", MARKER_REVIEW_VERSION, revision=3
    )
    assert len(again["marker_reviews"]) == 2
    with pytest.raises(ValueError, match="已失效"):
        store.record_sc_marker_review(
            "s", "m1", "rejected", "候选不足", "reviewer-b", MARKER_REVIEW_VERSION, revision=2
        )
    with pytest.raises(ValueError, match="不存在"):
        store.record_sc_marker_review(
            "other", "m1", "accepted", "ok", "reviewer-a", MARKER_REVIEW_VERSION, revision=3
        )


def test_cli_records_qc_cluster_and_marker_reviews(tmp_path):
    store = SessionStore(tmp_path / ".cellspatio" / "sessions.db")
    _seed(store, tmp_path)
    root = ["--root", str(tmp_path)]

    assert evidence.main(["review", "qc", "s", "r", "p1", "--reason", "阈值过宽", *root]) == 0
    assert evidence.main(["review", "cluster", "s", "r", "c1", "--reason", "簇不稳定", *root]) == 0
    assert (
        evidence.main(
            [
                "review",
                "marker",
                "s",
                "r",
                "m1",
                "--decision",
                "accepted",
                "--reason",
                "候选一致",
                *root,
            ]
        )
        == 0
    )
    assert (
        evidence.main(
            [
                "review",
                "qc",
                "s",
                "r",
                "p1",
                "--reason",
                "阈值过宽",
                "--decision",
                "accepted",
                *root,
            ]
        )
        == 2
    )

    run = store.get_qc_run("s", "r")
    assert run["previews"][0]["status"] == "rejected"
    assert run["cluster_rejections"][0]["reason"] == "簇不稳定"
    assert run["marker_reviews"][0]["decision"] == "accepted"
