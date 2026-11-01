"""第六片独立授权、快照/CAS、历史、保密与证据门禁；R 数值另验。"""

import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import src.analysis.sc_cluster as sc_cluster
import src.ui.session_store as session_store
from src.analysis.sc_cluster import (
    CLUSTER_TOOL_VERSION,
    PROTOCOL,
    checked_source,
    dimension_binding,
    epochs_for,
)
from src.api import routes_clusters
from src.api.routes_pca import DIMENSION_RECORD_VERSION
from tests.unit.test_sc_pca import PARAMS, table
from tests.unit.test_sc_pca_dimensions import dimensions_ready as _dimensions_ready
from tests.unit.test_sc_pca_dimensions import parent_ready as _parent_ready

dimensions_ready = _dimensions_ready


@pytest.fixture
def parent_ready(tmp_path, monkeypatch, request):
    """本片所有正常用例沿用 31 细胞父链。"""
    return _parent_ready.__wrapped__(
        tmp_path, monkeypatch, SimpleNamespace(param=getattr(request, "param", 31))
    )


def outputs(script):
    """仅正确结构的替身，不能证明 RDS 或真实科学结果。"""
    directory = Path(script).parent
    pca = Path(json.loads(Path(script).read_text().split("pca_dir <- ")[1].splitlines()[0]))
    barcodes = [r["barcode"] for r in csv.DictReader((pca / "pca_embeddings.csv").open())]
    n = len(barcodes)
    table(
        directory / "clusters.csv",
        ["barcode", "cluster_id"],
        [[b, i % 2] for i, b in enumerate(barcodes)],
    )
    table(
        directory / "umap_embeddings.csv",
        ["barcode", "UMAP_1", "UMAP_2"],
        [[b, i, -i] for i, b in enumerate(barcodes)],
    )
    table(
        directory / "cluster_sizes.csv",
        ["cluster_id", "cells", "proportion"],
        [[c, (n + 1 - c) // 2, ((n + 1 - c) // 2) / n] for c in range(2)],
    )
    for name in ("seurat_clustered.rds", "neighbors.rds"):
        (directory / name).write_bytes(b"mock-only")
    (directory / "umap_clusters.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (directory / "versions.txt").write_text(
        "\n".join(f"{k} {v}" for k, v in PROTOCOL["packages"].items()) + "\n"
    )
    (directory / "session_info.txt").write_text("mock-only")
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "cells": n,
                "dims": [1, 2],
                "n_epochs": epochs_for(n),
                "clusters": 2,
                "counts_unchanged": True,
                "pca_unchanged": True,
                "seurat_threads": 1,
                "nn_nonzero": n * 20,
                "snn_nonzero": n * 20,
                "future_version": "1.80.0",
            }
        )
    )
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


@pytest.fixture
def ready(dimensions_ready):
    """完整 API 记录确切 2-PC 前缀后才进入独立授权。"""
    d = dimensions_ready
    response = d.client.post(d.target, json={**d.body, "n_dims": 2})
    assert response.status_code == 200
    d.path = f"{d.url}/clusters/{d.fid}/{d.aid}"
    d.state = d.client.get(d.path).json()
    assert not d.state["blocked"]
    d.body = {
        "binding_sha256": d.state["binding_sha256"],
        "dimension_review_id": d.state["selection"]["review_id"],
        "revision": d.state["revision"],
        "reason": "私有临床依据不外发",
    }
    d.executor.reset_mock()
    d.executor.execute_script.side_effect = outputs
    return d


@pytest.fixture
def thirty_ready(tmp_path, monkeypatch):
    """独立构造 30 细胞父链，验证项目 31 细胞门禁。"""
    parent = _parent_ready.__wrapped__(tmp_path, monkeypatch, SimpleNamespace(param=30))
    d = _dimensions_ready.__wrapped__(parent)
    assert d.client.post(d.target, json={**d.body, "n_dims": 2}).status_code == 200
    d.path = f"{d.url}/clusters/{d.fid}/{d.aid}"
    return d


def execute(d):
    """授权并要求技术完成。"""
    response = d.client.post(d.path + "/execute", json=d.body)
    assert response.status_code == 200, response.text
    assert response.json()["attempts"][0]["status"] == "completed", response.json()
    return response.json()


def test_authorize_restore_idempotent_own_revision_and_no_leak(ready):
    d = ready
    before = {p: p.read_bytes() for p in [d.source, *d.root.parent.rglob("*")] if p.is_file()}
    messages = len(d.store.get_messages(d.sid))
    assert d.client.get(d.path).json() == d.state
    d.executor.execute_script.assert_not_called()
    state = execute(d)
    assert state["revision"] == d.body["revision"] + 2
    assert state["blocked"] == []
    assert state["selection"] == {
        "review_id": d.body["dimension_review_id"],
        "dims": [1, 2],
        "record_version": DIMENSION_RECORD_VERSION,
    }
    assert state["source_summary"] == {
        "cells": 31,
        "actual_pcs": state["source_summary"]["actual_pcs"],
        "n_epochs": 500,
    }
    assert state["binding_sha256"] is None
    assert state["attempts"][0]["invalidated_reason"] is None
    assert state["attempts"][0]["result"]["cells"] == 31
    assert d.client.get(d.path).json() == state
    assert d.client.post(d.path + "/execute", json=d.body).json() == state
    aid = state["attempts"][0]["attempt_id"]
    assert d.client.get(f"{d.path}/{aid}/diagnostic").status_code == 200
    d.executor.execute_script.assert_called_once()
    assert all(p.read_bytes() == data for p, data in before.items() if p.name != "sessions.db")
    assert len(d.store.get_messages(d.sid)) == messages + 1
    exposed = json.dumps(state) + json.dumps(d.store.get_messages(d.sid))
    assert d.body["reason"] not in exposed and str(d.source) not in exposed
    attempt = d.store.get_qc_run(d.sid, d.rid)["cluster_attempts"][0]
    assert attempt["reason"] == d.body["reason"]
    assert attempt["snapshot"]["review"]["reason"] == "私有维度依据"
    assert d.body["reason"] not in (Path(attempt["directory"]) / "script.R").read_text()


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("revision", 1.0),
        ("revision", -1),
        ("revision", 2**63),
        ("reason", " "),
        ("reason", "x" * 2001),
        ("extra", "ignored"),
        ("dims", [1, 2]),
        ("dimension_review_id", "wrong"),
    ],
)
def test_strict_request_rejected_without_r(ready, field, value):
    d = ready
    assert d.client.post(d.path + "/execute", json={**d.body, field: value}).status_code == 422
    d.executor.execute_script.assert_not_called()


def test_revision_and_association_conflicts(ready):
    d = ready
    for body, code in (
        ({**d.body, "revision": d.body["revision"] - 1}, 409),
        ({**d.body, "binding_sha256": "0" * 64}, 409),
        ({**d.body, "dimension_review_id": "0" * 32}, 404),
    ):
        assert d.client.post(d.path + "/execute", json=body).status_code == code
    other = d.store.create_session()
    assert d.client.get(d.path.replace(d.sid, other)).status_code == 404
    d.executor.execute_script.assert_not_called()


def test_running_guards_and_duplicate_post(ready):
    d = ready
    entered, release = Event(), Event()

    def waiting(script):
        entered.set()
        assert release.wait(30)
        return outputs(script)

    d.executor.execute_script.side_effect = waiting
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(d.client.post, d.path + "/execute", json=d.body)
        assert entered.wait(20)
        try:
            running = d.client.get(d.path).json()
            assert running["attempts"][0]["status"] == "running"
            assert running["selection"]["dims"] == [1, 2]
            assert running["source_summary"]["cells"] == 31
            assert running["blocked"] == []
            assert d.client.get(d.url).json()["cluster_running"]
            assert d.client.post(d.path + "/execute", json=d.body).status_code == 200
            assert d.client.post(d.url + "/preview", json=PARAMS).status_code == 409
            assert (
                d.client.post(
                    d.url + "/approve", json={"preview_id": d.pid, "reason": "x"}
                ).status_code
                == 409
            )
            dims = d.client.get(d.target).json()
            assert dims["current"] is None and dims["blocked"]
            assert d.client.post(d.target, json={**d.body, "n_dims": 2}).status_code in (409, 422)
            assert d.client.delete(f"/api/sessions/{d.sid}").status_code == 409
            with pytest.raises(ValueError, match="聚类"):
                d.store.delete_session(d.sid)
        finally:
            release.set()
        assert future.result().json()["attempts"][0]["status"] == "completed"
    d.executor.execute_script.assert_called_once()


def test_previous_attempt_is_rechecked_after_lock(ready, monkeypatch):
    d = ready
    run = d.store.get_qc_run(d.sid, d.rid)
    parent = next(a for a in run["attempts"] if a["attempt_id"] == d.fid)
    pca = next(a for a in run["pca_attempts"] if a["attempt_id"] == d.aid)
    review = next(
        r for r in run["dimension_reviews"] if r["review_id"] == d.body["dimension_review_id"]
    )
    snapshot = {
        "evidence": run["evidence"],
        "parent": parent,
        "pca": pca,
        "review": review,
        "source": checked_source(run, parent, pca, review, d.root, DIMENSION_RECORD_VERSION),
    }
    attempt_id = "race" * 8

    class RacingLock:
        def acquire(self, blocking=False):
            claimed = d.store.start_sc_cluster(
                d.sid,
                d.rid,
                d.body["revision"],
                snapshot,
                d.body["binding_sha256"],
                CLUSTER_TOOL_VERSION,
                PROTOCOL,
                d.body["reason"],
                str(d.root.parent / "sc_clusters" / attempt_id),
                attempt_id,
            )
            d.store.finish_sc_cluster(attempt_id, {"status": "completed"}, claimed)
            return True

        def release(self):
            return None

    monkeypatch.setattr(routes_clusters, "session_lock", lambda _: RacingLock())
    response = d.client.post(d.path + "/execute", json=d.body)
    assert response.status_code == 200
    assert response.json()["attempts"][0]["attempt_id"] == attempt_id
    d.executor.execute_script.assert_not_called()


@pytest.mark.parametrize("mutation", ["revision", "review", "parent", "attempt", "source"])
def test_live_mutation_fails_finish_without_restoring_revision(ready, mutation):
    d = ready

    def corrupt(script):
        result = outputs(script)
        with d.store._conn() as conn:
            if mutation == "revision":
                conn.execute(
                    "UPDATE qc_runs SET revision = revision + 10 WHERE run_id = ?", (d.rid,)
                )
            elif mutation == "review":
                conn.execute(
                    "UPDATE sc_pca_dimension_reviews SET reason = 'changed' WHERE review_id = ?",
                    (d.body["dimension_review_id"],),
                )
            elif mutation == "parent":
                conn.execute(
                    "UPDATE sc_filter_attempts SET binding_sha256 = 'changed' WHERE attempt_id = ?",
                    (d.fid,),
                )
            elif mutation == "attempt":
                conn.execute(
                    "UPDATE sc_cluster_attempts SET reason = 'changed' WHERE run_id = ?", (d.rid,)
                )
            else:
                d.source.write_bytes(b"changed")
        return result

    d.executor.execute_script.side_effect = corrupt
    state = d.client.post(d.path + "/execute", json=d.body).json()
    assert state["attempts"][0]["status"] == "failed"
    assert state["revision"] == d.body["revision"] + (12 if mutation == "revision" else 2)
    assert d.client.post(d.path + "/execute", json=d.body).status_code == 200
    d.executor.execute_script.assert_called_once()


@pytest.mark.parametrize(
    "filename",
    [
        "script.R",
        "neighbors.rds",
        "clusters.csv",
        "umap_embeddings.csv",
        "cluster_sizes.csv",
        "summary.json",
        "metadata.json",
        "umap_clusters.png",
        "versions.txt",
        "session_info.txt",
        "stdout.txt",
        "stderr.txt",
    ],
)
def test_changed_completed_files_are_history_and_no_diagnostic(ready, filename):
    d = ready
    state = execute(d)
    aid = state["attempts"][0]["attempt_id"]
    directory = Path(d.store.get_qc_run(d.sid, d.rid)["cluster_attempts"][0]["directory"])
    (directory / filename).write_bytes(b"corrupt")
    history = d.client.get(d.path).json()["attempts"][0]
    assert history["invalidated_reason"] and history["result"] is None
    assert d.client.get(f"{d.path}/{aid}/diagnostic").status_code == 409
    assert d.client.post(d.path + "/execute", json=d.body).status_code == 200
    d.executor.execute_script.assert_called_once()


def test_failure_then_explicit_new_review_can_authorize(ready):
    d = ready
    d.executor.execute_script.return_value = SimpleNamespace(
        returncode=1, stdout="", stderr="private"
    )
    d.executor.execute_script.side_effect = None
    state = d.client.post(d.path + "/execute", json=d.body).json()
    assert state["attempts"][0]["status"] == "failed"
    assert d.client.post(d.path + "/execute", json=d.body).json() == state
    dims = d.client.get(d.target).json()
    assert dims["current"] is None
    new = d.client.post(
        d.target,
        json={
            "binding_sha256": dims["binding_sha256"],
            "revision": dims["revision"],
            "n_dims": 2,
            "reason": "明确再次技术试验",
        },
    ).json()
    assert new["current"]["review_id"] != d.body["dimension_review_id"]
    state = d.client.get(d.path).json()
    assert state["binding_sha256"] != d.body["binding_sha256"]
    d.body = {
        **d.body,
        "dimension_review_id": state["selection"]["review_id"],
        "binding_sha256": state["binding_sha256"],
        "revision": state["revision"],
    }
    d.executor.execute_script.side_effect = outputs
    state = d.client.post(d.path + "/execute", json=d.body).json()
    assert [a["status"] for a in state["attempts"]] == ["failed", "completed"]
    assert d.executor.execute_script.call_count == 2


def test_unexpected_executor_error_is_safely_failed(ready):
    d = ready

    def crash(script):
        raise RuntimeError("private local path")

    d.executor.execute_script.side_effect = crash
    response = d.client.post(d.path + "/execute", json=d.body)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["attempts"][0]["status"] == "failed"
    assert state["attempts"][0]["result"] is None
    assert "private local path" not in response.text
    assert state["revision"] == d.body["revision"] + 2


@pytest.mark.parametrize(
    "column,value",
    [
        ("tool_version", "0.0.0"),
        ("protocol", json.dumps({**PROTOCOL, "seed": 43})),
    ],
)
def test_protocol_or_tool_drift_invalidates_history(ready, column, value):
    d = ready
    execute(d)
    with d.store._conn() as conn:
        conn.execute(
            f"UPDATE sc_cluster_attempts SET {column} = ? WHERE run_id = ?", (value, d.rid)
        )
    history = d.client.get(d.path).json()["attempts"][0]
    assert history["invalidated_reason"] and history["result"] is None


def test_thirty_cells_is_blocked_without_r(thirty_ready):
    d = thirty_ready
    state = d.client.get(d.path).json()
    assert state["binding_sha256"] is None
    assert "至少 31 细胞" in str(state["blocked"])
    d.executor.execute_script.assert_not_called()


def test_db_failure_does_not_execute_or_leak(ready, monkeypatch, caplog):
    d = ready

    def fail(*args):
        raise sqlite3.OperationalError("private path and clinical reason")

    monkeypatch.setattr(type(d.store), "start_sc_cluster", fail)
    response = d.client.post(d.path + "/execute", json=d.body)
    assert response.status_code == 409
    assert "clinical" not in response.text + caplog.text
    d.executor.execute_script.assert_not_called()


def test_finish_message_failure_rolls_back_terminal_state(ready, monkeypatch):
    """消息写入失败时终态、消息和 revision 必须一起回滚。"""
    d = ready
    before = len(d.store.get_messages(d.sid))
    revision = d.body["revision"]

    def fail(results):
        raise sqlite3.OperationalError("private message encoding failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(session_store, "_encode_results", fail)
        response = d.client.post(d.path + "/execute", json=d.body)
    assert response.status_code == 409
    assert "private message encoding failure" not in response.text
    run = d.store.get_qc_run(d.sid, d.rid)
    assert run["revision"] == revision + 1
    assert run["cluster_attempts"][0]["status"] == "running"
    assert len(d.store.get_messages(d.sid)) == before

    d.store.interrupt_sc_clusters()
    run = d.store.get_qc_run(d.sid, d.rid)
    assert run["cluster_attempts"][0]["status"] == "interrupted"
    assert run["revision"] == revision + 2
    assert len(d.store.get_messages(d.sid)) == before + 1


@pytest.mark.parametrize("kind", ["zero_norm", "nonfinite", "degenerate"])
def test_checked_source_rejects_invalid_selected_pc_values(tmp_path, monkeypatch, kind):
    """固定 cosine 协议不接受零范数、非有限或退化 PC。"""
    pca_dir = tmp_path / "pca"
    pca_dir.mkdir()
    rows = ["barcode,PC_1,PC_2"]
    for i in range(31):
        pc1, pc2 = float(i + 1), float(i + 2)
        if kind == "zero_norm" and i == 0:
            pc1, pc2 = 0.0, 0.0
        elif kind == "nonfinite" and i == 0:
            pc1 = float("nan")
        elif kind == "degenerate":
            pc2 = 1.0
        rows.append(f"b{i},{pc1},{pc2}")
    (pca_dir / "pca_embeddings.csv").write_text("\n".join(rows) + "\n")

    checked = {"artifacts": {}, "actual_pcs": 2}
    monkeypatch.setattr(sc_cluster, "checked_completed_pca", lambda *_: checked)
    run = {"session_id": "s", "run_id": "r"}
    parent = {"preview_id": "p", "attempt_id": "f"}
    pca = {"attempt_id": "a", "binding_sha256": "b", "directory": str(pca_dir)}
    review = {
        "pca_attempt_id": "a",
        "record_version": DIMENSION_RECORD_VERSION,
        "dims": [1, 2],
    }
    review["binding_sha256"] = dimension_binding(
        run, parent, pca, checked, DIMENSION_RECORD_VERSION
    )
    with pytest.raises(ValueError, match="零范数、非有限或退化"):
        checked_source(run, parent, pca, review, tmp_path, DIMENSION_RECORD_VERSION)


def test_interrupted_is_atomic_and_no_auto_resume(ready, monkeypatch):
    d = ready

    def stop(*args):
        raise sqlite3.OperationalError("simulated process loss after claim")

    with monkeypatch.context() as scoped:
        scoped.setattr(type(d.store), "finish_sc_cluster", stop)
        assert d.client.post(d.path + "/execute", json=d.body).status_code == 409
    messages = len(d.store.get_messages(d.sid))
    d.store.interrupt_sc_clusters()
    state = d.client.get(d.path).json()
    assert state["revision"] == d.body["revision"] + 2
    assert state["attempts"][0]["status"] == "interrupted"
    assert len(d.store.get_messages(d.sid)) == messages + 1
    d.store.interrupt_sc_clusters()
    assert len(d.store.get_messages(d.sid)) == messages + 1
    assert d.client.post(d.path + "/execute", json=d.body).json() == state
    d.executor.execute_script.assert_called_once()


def test_one_pc_is_blocked_not_autofilled(dimensions_ready):
    d = dimensions_ready
    assert d.client.post(d.target, json=d.body).status_code == 200
    path = f"{d.url}/clusters/{d.fid}/{d.aid}"
    state = d.client.get(path).json()
    assert state["blocked"] and state["binding_sha256"] is None
    assert "至少 2" in str(state["blocked"])
    d.executor.execute_script.assert_not_called()


def test_epoch_boundary():
    """项目固定规则不能被默认值或运行成本改变。"""
    assert epochs_for(10000) == 500 and epochs_for(10001) == 200
    assert PROTOCOL["timeout_seconds"] == 300
