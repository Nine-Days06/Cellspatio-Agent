"""第四片授权/持久化/产物的独立门禁；R 数值另做集成测试。"""

import csv
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.analysis.qc_preview import _identity
from src.analysis.r_executor import RExecutorError
from src.analysis.sc_pca import PCA_TOOL_VERSION, PROTOCOL, checked_parent
from src.analysis.sc_qc import run_sc_qc
from src.api.app import create_app
from src.api.chat_service import run_turn
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent, FakeRuntime

PARAMS = {"n_feature_min": 1, "n_feature_max": 3, "percent_mt_max": 100}


def table(path, fields, rows):
    """保序写微型测试表。"""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)


def outputs(script):
    """结构正确的替身产物，不能代替真实 R 数值验收。"""
    directory = Path(script).parent
    source = json.loads(Path(script).read_text().split("parent_dir <- ")[1].splitlines()[0])
    parent = Path(source)
    rows = list(csv.DictReader((parent / "features.csv").open()))
    keys = [f"F{i:08d}" for i in range(1, 4)]
    fields = [*rows[0], "parent_rowname", "seurat_feature_key"]
    mapping = [[*r.values(), r["unique_name"], k] for r, k in zip(rows, keys, strict=True)]
    table(directory / "feature_mapping.csv", fields, mapping)
    table(
        directory / "variable_features.csv",
        [*fields, "usable", "excluded_reason"],
        [[*r, "TRUE", ""] for r in mapping],
    )
    table(
        directory / "pca_embeddings.csv",
        ["barcode", "PC_1", "PC_2"],
        [[b, 1, 2] for b in ("c1", "c2", "c3")],
    )
    table(
        directory / "pca_loadings.csv",
        ["seurat_feature_key", "PC_1", "PC_2"],
        [[k, 0.5, 0.3] for k in keys],
    )
    table(directory / "pca_stdev.csv", ["pc", "stdev"], [["PC_1", 2], ["PC_2", 1]])
    (directory / "seurat_pca.rds").write_bytes(b"mock-pca")
    (directory / "pca_diagnostics.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (directory / "versions.txt").write_text(
        "\n".join(f"{k} {v}" for k, v in PROTOCOL["packages"].items()) + "\n"
    )
    (directory / "session_info.txt").write_text("test namespace only")
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "cells": 3,
                "features": 3,
                "selected_features": 3,
                "usable_features": 3,
                "actual_pcs": 2,
                "requested_pcs_max": 30,
                "pc_reduction_reason": "cell_or_usable_feature_limit",
                "counts_unchanged": True,
            }
        )
    )
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


@pytest.fixture
def parent_ready(tmp_path, monkeypatch):
    """走已有固定 QC、注册、预览、批准、过滤 API；仅 R 替身。"""
    source = tmp_path / "counts.csv"
    source.write_text("gene,c1,c2,c3\nMT-X,1,2,3\nA_B,2,3,1\nA|B,3,1,2\n")
    root = tmp_path / "data/analysis/sc_qc"

    def qc_output(script):
        directory = Path(script).parent
        table(
            directory / "features.csv",
            ["feature_id", "symbol", "unique_name"],
            [[g, g, g] for g in ("MT-X", "A_B", "A|B")],
        )
        table(
            directory / "cell_qc.csv",
            ["barcode", "nCount_RNA", "nFeature_RNA", "percent_mt", "missing_reason"],
            [[b, 6, 3, 100 / 6 * i, ""] for i, b in enumerate(("c1", "c2", "c3"), 1)],
        )
        (directory / "versions.txt").write_text("R 4.6.1\nSeurat 5.6.0\nMatrix 1.7.5\n")
        (directory / "qc.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    executor = Mock()
    executor.execute_script.side_effect = qc_output
    result = run_sc_qc(str(source), executor, root)
    assert result["status"] == "needs_confirmation"
    agent = FakeAgent(runtime=FakeRuntime(default=result))
    agent.workflow_manager = SimpleNamespace(
        workflow_recorder=SimpleNamespace(repo_root=tmp_path), r_executor=executor
    )
    store = SessionStore()
    sid = store.create_session()
    run_turn(agent, store, sid, "QC", lambda _: True)
    rid = result["results"]["qc_run_id"]
    monkeypatch.setenv("OLLAMA_EAGER_START", "0")
    client = TestClient(create_app(agent))
    url = f"/api/sessions/{sid}/qc/{rid}"
    pid = client.post(url + "/preview", json=PARAMS).json()["active_preview"]["preview_id"]
    assert (
        client.post(url + "/approve", json={"preview_id": pid, "reason": "技术测试"}).status_code
        == 200
    )

    def filter_output(script):
        directory = Path(script).parent
        table(directory / "barcodes.csv", ["barcode"], [[b] for b in ("c1", "c2", "c3")])
        (directory / "dimensions.txt").write_text("3\n3\n9\n")
        (directory / "versions.txt").write_text("R 4.6.1\nSeurat 5.6.0\nMatrix 1.7.5\n")
        (directory / "filtered_counts.rds").write_bytes(b"mock-counts")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    executor.execute_script.side_effect = filter_output
    response = client.post(url + "/filter", json={"preview_id": pid})
    assert response.json()["attempts"][0]["status"] == "completed"
    fid = response.json()["attempts"][0]["attempt_id"]
    executor.reset_mock()
    executor.execute_script.side_effect = outputs
    pca_url = url + "/pca/" + fid
    binding = client.get(pca_url).json()["binding_sha256"]
    assert binding and not client.get(pca_url).json()["blocked"]
    return store, sid, rid, root, source, client, url, pid, fid, pca_url, binding, executor


def test_get_never_executes_post_once_and_private_stage_only(parent_ready):
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready
    original = {p: p.read_bytes() for p in [source, *(root / rid).glob("*.csv")]}
    for _ in range(2):
        assert not client.get(path).json()["attempts"]
    executor.execute_script.assert_not_called()
    body = {"binding_sha256": binding, "reason": "私有依据"}
    response = client.post(path, json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["attempts"][0]["status"] == "completed"
    assert data["attempts"][0]["result"]["actual_pcs"] == 2
    assert client.post(path, json=body).json() == data
    assert client.get(path).json() == data
    executor.execute_script.assert_called_once()
    assert all(p.read_bytes() == content for p, content in original.items())
    run = store.get_qc_run(sid, rid)
    attempt = run["pca_attempts"][0]
    assert attempt["reason"] == "私有依据" and json.loads(attempt["protocol"]) == PROTOCOL
    assert attempt["result"]["stage"] == "pca_review"
    messages = store.get_messages(sid)
    assert messages[-1]["results"]["sc_pca"]["scientific_status"] == "unreviewed"
    assert "私有依据" not in json.dumps(messages, ensure_ascii=False)
    assert "pca_embeddings" not in json.dumps(messages)
    assert "directory" not in data["attempts"][0]
    image_path = path + "/" + attempt["attempt_id"] + "/diagnostic"
    assert client.get(image_path).headers["cache-control"] == "no-store"
    image = Path(attempt["directory"]) / "pca_diagnostics.png"
    image.write_bytes(image.read_bytes() + b"changed")
    assert client.get(image_path).status_code == 409
    assert client.get(path + "/unknown/diagnostic").status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        "input",
        "qc",
        "rds",
        "selection",
        "metadata",
        "new_preview",
        "script",
        "versions",
        "barcodes",
        "directory",
        "tool_version",
    ],
)
def test_parent_changes_fail_closed(parent_ready, change):
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready
    run = store.get_qc_run(sid, rid)
    parent = Path(run["attempts"][0]["directory"])
    files = {
        "input": source,
        "qc": root / rid / "cell_qc.csv",
        "rds": parent / "filtered_counts.rds",
        "selection": parent / "selection.csv",
        "metadata": parent / "metadata.json",
        "script": parent / "script.R",
        "versions": parent / "versions.txt",
        "barcodes": parent / "barcodes.csv",
    }
    if change in files:
        files[change].write_bytes(files[change].read_bytes() + b"changed")
    elif change == "new_preview":
        assert client.post(url + "/preview", json=PARAMS).status_code == 200
    else:
        with store._conn() as conn:
            conn.execute(
                f"UPDATE sc_filter_attempts SET {change} = ? WHERE attempt_id = ?", ("bad", fid)
            )
    assert client.get(path).json()["blocked"]
    assert client.post(path, json={"binding_sha256": binding, "reason": "test"}).status_code == 409
    executor.execute_script.assert_not_called()
    assert not store.get_qc_run(sid, rid)["pca_attempts"]


def test_cross_session_parameters_and_binding_rejected(parent_ready):
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready
    other = store.create_session()
    assert client.get(path.replace(sid, other)).status_code == 404
    assert (
        client.post(
            path.replace(sid, other), json={"binding_sha256": binding, "reason": "test"}
        ).status_code
        == 404
    )
    assert client.get(path.replace(fid, "unknown")).status_code == 404
    for body in (
        {"binding_sha256": binding, "reason": " "},
        {"binding_sha256": "x", "reason": "test"},
        {"binding_sha256": binding, "reason": "test", "script": "stop()"},
        {"binding_sha256": binding, "reason": "test", "npcs": 3},
    ):
        assert client.post(path, json=body).status_code == 422
    assert client.post(path, json={"binding_sha256": "0" * 64, "reason": "test"}).status_code == 409
    executor.execute_script.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    ["R", "timeout", "missing", "nonfinite", "reorder", "parent_during", "output_creation"],
)
def test_failure_no_retry_and_logs_preserved(parent_ready, failure, monkeypatch):
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready

    def failed(script):
        if failure in ("R", "timeout"):
            exc = RExecutorError("failure", stdout="partial", stderr="diagnostic")
            if failure == "timeout":
                exc.__cause__ = subprocess.TimeoutExpired(
                    "Rscript", 300, output=b"partial", stderr=b"timeout"
                )
            raise exc
        if failure == "missing":
            return SimpleNamespace(returncode=0, stdout="partial", stderr="")
        result = outputs(script)
        directory = Path(script).parent
        if failure == "nonfinite":
            table(directory / "pca_stdev.csv", ["pc", "stdev"], [["PC_1", "NaN"], ["PC_2", 1]])
        elif failure == "reorder":
            table(
                directory / "pca_embeddings.csv",
                ["barcode", "PC_1", "PC_2"],
                [[b, 1, 2] for b in ("c2", "c1", "c3")],
            )
        elif failure == "parent_during":
            source.write_bytes(source.read_bytes() + b"changed")
        return result

    executor.execute_script.side_effect = failed
    if failure == "output_creation":
        original = Path.mkdir

        def denied(self, *args, **kwargs):
            if self.parent.name == "sc_pca":
                raise PermissionError("test cannot create")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", denied)
    body = {"binding_sha256": binding, "reason": "test"}
    assert client.post(path, json=body).json()["attempts"][0]["status"] == "failed"
    assert client.post(path, json=body).json()["attempts"][0]["status"] == "failed"
    assert len(store.get_qc_run(sid, rid)["pca_attempts"]) == 1
    assert executor.execute_script.call_count == (0 if failure == "output_creation" else 1)
    attempt = store.get_qc_run(sid, rid)["pca_attempts"][0]
    if failure in ("R", "timeout"):
        directory = Path(attempt["directory"])
        assert "partial" in (directory / "stdout.txt").read_text()
        assert (directory / "stderr.txt").read_text()
    assert client.get(path + "/" + attempt["attempt_id"] + "/diagnostic").status_code == 404


def test_multi_connection_claim_running_guards_restart_and_atomic_finish(parent_ready):
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready
    run = store.get_qc_run(sid, rid)
    barrier = Barrier(2)

    def claim(index):
        barrier.wait()
        return store.start_sc_pca(
            sid,
            rid,
            run["revision"],
            fid,
            binding,
            PCA_TOOL_VERSION,
            PROTOCOL,
            "test",
            str(root.parent / "sc_pca" / str(index)),
            str(index),
        )

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, [1, 2]))
    assert sum(c["claimed"] for c in claims) == 1
    assert client.get(url).json()["pca_running"]
    assert client.post(url + "/preview", json=PARAMS).status_code == 409
    assert (
        client.post(url + "/approve", json={"preview_id": pid, "reason": "test"}).status_code == 409
    )
    assert client.delete(f"/api/sessions/{sid}").status_code == 409
    attempt_id = store.get_qc_run(sid, rid)["pca_attempts"][0]["attempt_id"]
    count = len(store.get_messages(sid))
    with store._conn() as conn:
        conn.execute(
            "CREATE TRIGGER reject_pca BEFORE UPDATE OF status ON sc_pca_attempts BEGIN SELECT RAISE(ABORT, 'forced rollback'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError, match="forced rollback"):
        store.finish_sc_pca(attempt_id, {"status": "completed"})
    assert len(store.get_messages(sid)) == count
    assert store.get_qc_run(sid, rid)["pca_attempts"][0]["status"] == "running"
    with store._conn() as conn:
        conn.execute("DROP TRIGGER reject_pca")
    with TestClient(create_app(client.app.state.agent)) as restarted:
        assert restarted.get(path).json()["attempts"][0]["status"] == "interrupted"
        assert (
            restarted.post(path, json={"binding_sha256": binding, "reason": "test"}).json()[
                "attempts"
            ][0]["status"]
            == "interrupted"
        )
    executor.execute_script.assert_not_called()


def test_parallel_api_one_r_and_live_get(parent_ready):
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready
    entered, release = Event(), Event()

    def slow(script):
        entered.set()
        assert release.wait(15)
        return outputs(script)

    executor.execute_script.side_effect = slow
    body = {"binding_sha256": binding, "reason": "test"}
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(client.post, path, json=body)
        try:
            assert entered.wait(10)
            assert client.get(path).json()["attempts"][0]["status"] == "running"
            assert client.post(path, json=body).json()["attempts"][0]["status"] == "running"
            assert client.post(path, json={**body, "binding_sha256": "0" * 64}).status_code == 409
            assert client.post(url + "/preview", json=PARAMS).status_code == 409
        finally:
            release.set()
        assert future.result().json()["attempts"][0]["status"] == "completed"
    executor.execute_script.assert_called_once()


def test_semantic_parent_checks_even_with_replaced_hash(parent_ready):
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready
    run = store.get_qc_run(sid, rid)
    parent = run["attempts"][0]
    file = Path(parent["directory"]) / "selection.csv"
    file.write_bytes(b"barcode,keep,reasons\nc1,0,x\nc2,1,\nc3,1,\n")
    parent["result"]["artifacts"]["selection.csv"] = {
        "path": str(file),
        **_identity(file.read_bytes()),
    }
    (file.parent / "metadata.json").write_text(json.dumps(parent["result"]))
    with pytest.raises(ValueError, match="选择副本"):
        checked_parent(run, parent, root)
