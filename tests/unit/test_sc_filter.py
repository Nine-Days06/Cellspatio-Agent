"""固定过滤授权、证据、幂等和失败；真实 R 另由集成测试验证。"""

import csv
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.analysis.qc_preview import _identity
from src.analysis.r_executor import RExecutorError
from src.analysis.sc_filter import FILTER_TOOL_VERSION, verified_selection
from src.api.app import create_app
from tests.unit.test_qc_preview import PARAMS
from tests.unit.test_qc_preview import registered as registered


@pytest.fixture
def ready(registered):
    """通过真实 QC 注册/API 预览与批准，并安装受控 R 产物替身。"""
    store, sid, rid, root, source, client = registered
    url = f"/api/sessions/{sid}/qc/{rid}"
    data = client.post(url + "/preview", json=PARAMS).json()
    pid = data["active_preview"]["preview_id"]
    assert (
        client.post(url + "/approve", json={"preview_id": pid, "reason": "技术测试"}).status_code
        == 200
    )

    def execute(script):
        directory = Path(script).parent
        with (directory / "selection.csv").open(newline="") as handle:
            kept = [r["barcode"] for r in csv.DictReader(handle) if r["keep"] == "1"]
        with (directory / "barcodes.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["barcode"])
            writer.writerows([b] for b in kept)
        (directory / "dimensions.txt").write_text(f"2\n{len(kept)}\n2\n")
        (directory / "versions.txt").write_text("R 4.6.1\nSeurat 5.6.0\nMatrix 1.7.5\n")
        (directory / "filtered_counts.rds").write_bytes(b"mock-rds")
        return SimpleNamespace(returncode=0, stdout="filter ok", stderr="")

    executor = Mock()
    executor.execute_script.side_effect = execute
    client.app.state.agent.workflow_manager.r_executor = executor
    return store, sid, rid, root, source, client, url, pid, executor


def test_filter_once_exact_binding_and_stage_only(ready):
    store, sid, rid, root, source, client, url, pid, executor = ready
    before = source.read_bytes()
    response = client.post(url + "/filter", json={"preview_id": pid})
    assert response.status_code == 200, response.text
    attempt = response.json()["attempts"][0]
    assert attempt["status"] == "completed" and attempt["result"]["cells"] == 1
    assert attempt["result"]["scientific_status"] == "unreviewed"
    assert source.read_bytes() == before
    assert (
        client.post(url + "/filter", json={"preview_id": pid}).json()["attempts"]
        == response.json()["attempts"]
    )
    assert executor.execute_script.call_count == 1
    message = store.get_messages(sid)[-1]
    assert "未执行归一化或聚类" in message["content"]
    assert set(message["results"]["sc_filter"]) == {
        "run_id",
        "attempt_id",
        "stage",
        "technical_status",
        "scientific_status",
    }
    assert client.get(url).json()["attempts"][0]["status"] == "completed"


@pytest.mark.parametrize("mutation", ["input", "qc", "selection", "new_preview", "unapproved"])
def test_changed_or_unapproved_selection_never_runs(ready, mutation):
    store, sid, rid, root, source, client, url, pid, executor = ready
    run = store.get_qc_run(sid, rid)
    if mutation == "input":
        source.write_text("gene,c1,c2\nMT-X,2,1\nG1,0,3\n")
    elif mutation == "qc":
        (Path(run["directory"]) / "cell_qc.csv").write_bytes(b"bad")
    elif mutation == "selection":
        (Path(run["directory"]) / "previews" / pid / "selection.csv").write_bytes(b"bad")
    elif mutation == "new_preview":
        client.post(url + "/preview", json=PARAMS)
    else:
        pid = client.post(url + "/preview", json=PARAMS).json()["active_preview"]["preview_id"]
    assert client.post(url + "/filter", json={"preview_id": pid}).status_code == 409
    executor.execute_script.assert_not_called()


def test_cross_session_and_arbitrary_code_rejected(ready):
    store, sid, rid, root, source, client, url, pid, executor = ready
    other = store.create_session()
    assert (
        client.post(f"/api/sessions/{other}/qc/{rid}/filter", json={"preview_id": pid}).status_code
        == 404
    )
    assert (
        client.post(url + "/filter", json={"preview_id": pid, "script": "stop()"}).status_code
        == 422
    )
    executor.execute_script.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["missing", "nonzero", "timeout", "barcode", "version", "mutate_input", "script"]
)
def test_failures_are_persistent_and_never_auto_retry(ready, failure):
    store, sid, rid, root, source, client, url, pid, executor = ready
    original = executor.execute_script.side_effect

    def execute(script):
        directory = Path(script).parent
        if failure == "timeout":
            error = RExecutorError("timed out")
            error.__cause__ = subprocess.TimeoutExpired(
                "Rscript", 300, output=b"partial", stderr=b"timed"
            )
            raise error
        result = original(script)
        if failure == "missing":
            (directory / "filtered_counts.rds").unlink()
        elif failure == "nonzero":
            return SimpleNamespace(returncode=1, stdout="partial", stderr="failed")
        elif failure == "barcode":
            (directory / "barcodes.csv").write_text("barcode\nwrong\n")
        elif failure == "version":
            (directory / "versions.txt").write_text("R changed\n")
        elif failure == "mutate_input":
            source.write_text("gene,c1,c2\nMT-X,2,1\nG1,0,3\n")
        elif failure == "script":
            Path(script).write_text("changed")
        return result

    executor.execute_script.side_effect = execute
    response = client.post(url + "/filter", json={"preview_id": pid})
    assert response.status_code == 200, response.text
    attempt = response.json()["attempts"][0]
    assert attempt["status"] == "failed" and attempt["result"]["error"]
    if failure == "timeout":
        assert (Path(attempt["directory"]) / "stdout.txt").read_text() == "partial"
        assert (Path(attempt["directory"]) / "stderr.txt").read_text() == "timed"
    client.post(url + "/filter", json={"preview_id": pid})
    assert executor.execute_script.call_count == 1
    assert "过滤失败" in store.get_messages(sid)[-1]["content"]


def test_output_creation_failure_finishes_failed_not_stuck_running(ready, monkeypatch):
    """无法创建证据目录时仍落盘失败状态，不能挂在 running。"""
    store, sid, rid, root, source, client, url, pid, executor = ready

    def failed(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("src.api.routes_qc.run_sc_filter", failed)
    response = client.post(url + "/filter", json={"preview_id": pid})
    assert response.status_code == 200
    assert response.json()["attempts"][0]["status"] == "failed"
    assert response.json()["attempts"][0]["result"]["error"] == "disk full"
    executor.execute_script.assert_not_called()


def test_store_cas_double_claim_running_blocks_mutation_and_restart(ready):
    store, sid, rid, root, source, client, url, pid, executor = ready
    run = store.get_qc_run(sid, rid)
    binding = run["previews"][0]["binding_sha256"]
    barrier = Barrier(2)

    def claim(index):
        barrier.wait()
        return store.start_sc_filter(
            sid,
            rid,
            run["revision"],
            pid,
            binding,
            FILTER_TOOL_VERSION,
            str(root.parent / "sc_filter" / str(index)),
            str(index),
        )

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, [1, 2]))
    assert sum(c["claimed"] for c in claims) == 1
    assert client.post(url + "/preview", json=PARAMS).status_code == 409
    assert (
        client.post(url + "/approve", json={"preview_id": pid, "reason": "again"}).status_code
        == 409
    )
    assert client.delete(f"/api/sessions/{sid}").status_code == 409
    with TestClient(create_app(client.app.state.agent)) as restarted:
        assert restarted.get(url).json()["attempts"][0]["status"] == "interrupted"
        assert (
            restarted.post(url + "/filter", json={"preview_id": pid}).json()["attempts"][0][
                "status"
            ]
            == "interrupted"
        )
    executor.execute_script.assert_not_called()


def test_selection_recomputed_even_if_artifact_identity_replaced(ready):
    store, sid, rid, root, source, client, url, pid, executor = ready
    run = store.get_qc_run(sid, rid)
    preview = run["previews"][0]
    path = Path(run["directory"]) / "previews" / pid / "selection.csv"
    path.write_bytes(b"barcode,keep,reasons\nc1,1,\nc2,0,mt_above_max\n")
    preview["artifacts"]["selection.csv"] = _identity(path.read_bytes())
    with pytest.raises(ValueError, match="重算"):
        verified_selection(run, root, preview)


def test_finish_state_message_transaction_rolls_back(ready):
    store, sid, rid, root, source, client, url, pid, executor = ready
    run = store.get_qc_run(sid, rid)
    store.start_sc_filter(
        sid,
        rid,
        run["revision"],
        pid,
        run["previews"][0]["binding_sha256"],
        FILTER_TOOL_VERSION,
        str(root.parent / "sc_filter" / "fail"),
        "fail",
    )
    count = len(store.get_messages(sid))
    with store._conn() as conn:
        conn.execute(
            "CREATE TRIGGER reject_filter BEFORE UPDATE OF status ON sc_filter_attempts BEGIN SELECT RAISE(ABORT, 'forced rollback'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError, match="forced rollback"):
        store.finish_sc_filter("fail", {"status": "completed"})
    assert len(store.get_messages(sid)) == count
    assert store.get_qc_run(sid, rid)["attempts"][0]["status"] == "running"


def test_parallel_api_only_one_r_and_live_get_is_running(ready):
    """慢 R 期间其他 API 可读状态，但不能改变选择或删除。"""
    from threading import Event

    store, sid, rid, root, source, client, url, pid, executor = ready
    original = executor.execute_script.side_effect
    started, release = Event(), Event()

    def execute(script):
        started.set()
        assert release.wait(10)
        return original(script)

    executor.execute_script.side_effect = execute
    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(client.post, url + "/filter", json={"preview_id": pid})
        assert started.wait(10)
        assert client.get(url).json()["attempts"][0]["status"] == "running"
        assert (
            client.post(url + "/filter", json={"preview_id": pid}).json()["attempts"][0]["status"]
            == "running"
        )
        assert client.post(url + "/preview", json=PARAMS).status_code == 409
        assert client.delete(f"/api/sessions/{sid}").status_code == 409
        release.set()
        assert task.result().json()["attempts"][0]["status"] == "completed"
    assert executor.execute_script.call_count == 1
