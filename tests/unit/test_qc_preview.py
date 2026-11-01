"""阈值边界、证据绑定、会话/API 和 SQLite 短事务竞态。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.analysis.qc_preview import (
    Thresholds,
    build_preview,
    check_preview,
    decisions,
    register_evidence,
)
from src.analysis.sc_qc import run_sc_qc
from src.api.app import create_app
from src.api.chat_service import run_turn
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent, FakeRuntime

PARAMS = {"n_feature_min": 1, "n_feature_max": 2, "percent_mt_max": 25}
HEADER = b"barcode,nCount_RNA,nFeature_RNA,percent_mt,missing_reason\n"


@pytest.fixture
def registered(tmp_path, monkeypatch):
    """走固定 QC 与真实 run_turn 认领链，只有 R 产物用微型数据替代。"""
    source = tmp_path / "counts.csv"
    source.write_text("gene,c1,c2\nMT-X,1,1\nG1,0,3\n")
    root = tmp_path / "data/analysis/sc_qc"

    def execute(script):
        directory = Path(script).parent
        (directory / "features.csv").write_text(
            "feature_id,symbol,unique_name\nMT-X,MT-X,MT-X\nG1,G1,G1\n"
        )
        (directory / "cell_qc.csv").write_bytes(HEADER + b"c1,1,1,100,\nc2,4,2,25,\n")
        (directory / "versions.txt").write_text("R 4.6.1\nSeurat 5.6.0\nMatrix 1.7.5\n")
        (directory / "qc.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    executor = Mock()
    executor.execute_script.side_effect = execute
    result = run_sc_qc(str(source), executor, root)
    assert result["status"] == "needs_confirmation"
    agent = FakeAgent(runtime=FakeRuntime(default=result))
    agent.workflow_manager = SimpleNamespace(workflow_recorder=SimpleNamespace(repo_root=tmp_path))
    store = SessionStore()
    sid = store.create_session()
    run_turn(agent, store, sid, "QC", lambda _: True)
    run_id = result["results"]["qc_run_id"]
    monkeypatch.setenv("OLLAMA_EAGER_START", "0")
    client = TestClient(create_app(agent))
    return store, sid, run_id, root, source, client


@pytest.mark.parametrize(
    "bad",
    [
        {"n_feature_min": True},
        {"n_feature_min": "1"},
        {"n_feature_min": 1.5},
        {"n_feature_min": -1},
        {"n_feature_min": 3},
        {"percent_mt_max": "25"},
        {"percent_mt_max": True},
        {"percent_mt_max": float("nan")},
        {"percent_mt_max": 101},
        {"n_count_min": 2, "n_count_max": 1},
        {"extra": 2},
    ],
)
def test_rejects_invalid_thresholds(bad):
    with pytest.raises(ValidationError):
        Thresholds(**(PARAMS | bad))


def test_inclusive_decimal_boundaries_and_overlapping_reasons():
    rows, summary = decisions(
        HEADER + b"a,4,2,25,\nb,1,1,25.00000000000000001,\nc,6,3,50,\n",
        Thresholds(**PARAMS, n_count_min=4, n_count_max=4),
    )
    assert rows[0]["keep"]
    assert rows[1]["reasons"] == ["n_count_below_min", "mt_above_max"]
    assert rows[2]["reasons"] == ["n_feature_above_max", "n_count_above_max", "mt_above_max"]
    assert summary["kept"] == 1 and summary["excluded"] == 2
    assert summary["reason_counts"]["mt_above_max"] == 2
    assert sum(summary["reason_counts"].values()) > summary["excluded"]
    assert (
        Thresholds(**PARAMS).canonical()
        == Thresholds(**(PARAMS | {"percent_mt_max": 25.0})).canonical()
    )
    assert Thresholds(**(PARAMS | {"percent_mt_max": 0.01})).canonical()["percent_mt_max"] == "0.01"


@pytest.mark.parametrize("data", [b"a,0,0,NA,zero_counts\n", b"a,4,2,NA,mt_symbol_unresolved\n"])
def test_undefined_mt_and_zero_retained_block(data):
    _, summary = decisions(HEADER + data, Thresholds(**PARAMS))
    assert summary["blocked"] and summary["kept"] == 0


@pytest.mark.parametrize(
    "data",
    [
        b"bad,header\n",
        HEADER,
        HEADER + b"a,4,2,NaN,\n",
        HEADER + b"a,4,2,25,\na,4,2,25,\n",
        HEADER + b"a,4,2,NA,\n",
    ],
)
def test_malformed_qc_table_is_rejected(data):
    with pytest.raises(ValueError):
        decisions(data, Thresholds(**PARAMS))


def test_api_registration_preview_approval_restart_and_idempotency(registered):
    store, sid, rid, root, source, client = registered
    path = f"/api/sessions/{sid}/qc/{rid}"
    before = source.read_bytes()
    assert store.get_messages(sid)[-1]["results"]["qc_run_id"] == rid
    preview = client.post(path + "/preview", json=PARAMS).json()["active_preview"]
    assert preview["summary"]["kept"] == 1 and preview["summary"]["filtered"] is False
    selection = root / rid / "previews" / preview["preview_id"] / "selection.csv"
    assert "c2,1," in selection.read_text()
    body = {"preview_id": preview["preview_id"], "reason": "  人工审阅分布  "}
    approved = client.post(path + "/approve", json=body)
    assert approved.status_code == 200
    original = approved.json()["approvals"]
    count = len(store.get_messages(sid))
    assert original[0]["reason"] == "人工审阅分布"
    assert client.post(path + "/approve", json=body).json()["approvals"] == original
    assert len(SessionStore().get_messages(sid)) == count
    assert client.get(path).json()["approvals"] == original
    assert source.read_bytes() == before
    # 新预览不删除已批准历史；重复旧批准仍返回原事实，不产生新消息。
    assert client.post(path + "/preview", json=PARAMS | {"percent_mt_max": 100}).status_code == 200
    assert client.post(path + "/approve", json=body).json()["approvals"] == original
    assert len(store.get_messages(sid)) == count
    store.delete_session(sid)
    assert store.get_qc_run(sid, rid) is None and selection.is_file()


@pytest.mark.parametrize(
    "change",
    [
        "input",
        "cell_qc.csv",
        "features.csv",
        "script.R",
        "summary.json",
        "versions.txt",
        "qc.png",
        "metadata.json",
        "selection.csv",
        "preview.json",
    ],
)
def test_evidence_replacement_blocks_approval(registered, change):
    store, sid, rid, root, source, client = registered
    url = f"/api/sessions/{sid}/qc/{rid}"
    preview = client.post(url + "/preview", json=PARAMS).json()["active_preview"]
    target = (
        source
        if change == "input"
        else (
            root / rid / change
            if change not in ("selection.csv", "preview.json")
            else root / rid / "previews" / preview["preview_id"] / change
        )
    )
    target.write_bytes(target.read_bytes() + b"changed")
    response = client.post(
        url + "/approve", json={"preview_id": preview["preview_id"], "reason": "review"}
    )
    assert response.status_code == 409
    assert not store.get_qc_run(sid, rid)["previews"][0]["approved_at"]


def test_missing_association_cross_session_invalid_parameters_stale_and_blocked(registered):
    store, sid, rid, root, source, client = registered
    path = f"/api/sessions/{sid}/qc/{rid}"
    other = store.create_session()
    for method, suffix, body in [
        ("get", "", None),
        ("post", "/preview", PARAMS),
        ("post", "/approve", {"preview_id": "fake", "reason": "review"}),
    ]:
        assert (
            client.request(method, f"/api/sessions/{other}/qc/{rid}{suffix}", json=body).status_code
            == 404
        )
    assert client.get(f"/api/sessions/{sid}/qc/old-unregistered").status_code == 404
    assert client.post(path + "/preview", json=PARAMS | {"n_feature_min": True}).status_code == 422
    assert (
        client.post(path + "/approve", json={"preview_id": "a", "reason": "   "}).status_code == 422
    )
    p1 = client.post(path + "/preview", json=PARAMS).json()["active_preview"]
    p2 = client.post(
        path + "/preview",
        json=PARAMS | {"n_feature_min": 2, "n_feature_max": 2, "percent_mt_max": 0},
    ).json()["active_preview"]
    assert p2["summary"]["blocked"]
    for p in (p1, p2):
        assert (
            client.post(
                path + "/approve", json={"preview_id": p["preview_id"], "reason": "review"}
            ).status_code
            == 409
        )
    with pytest.raises(ValueError):
        register_evidence(root / rid, root.parent)
    with pytest.raises(sqlite3.IntegrityError):
        store.register_qc_run(other, rid, str(root / rid), {})
    source.write_bytes(source.read_bytes() + b"\n")
    assert client.get(path).json()["blocked"]


def test_multi_connection_preview_cas_and_double_approval(registered):
    store, sid, rid, root, _, _ = registered
    run = store.get_qc_run(sid, rid)
    p1, p2 = [build_preview(run, root, Thresholds(**PARAMS)) for _ in range(2)]
    gate = Barrier(2)

    def activate(preview):
        gate.wait()
        try:
            SessionStore().activate_qc_preview(sid, rid, 0, preview)
            return preview
        except ValueError:
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(activate, [p1, p2]))
    winner = next(p for p in outcomes if p)
    assert sum(p is not None for p in outcomes) == 1
    run = store.get_qc_run(sid, rid)
    check_preview(run, root, winner)
    gate = Barrier(2)

    def approve(_):
        gate.wait()
        return SessionStore().approve_qc_preview(
            sid, rid, run["revision"], winner["preview_id"], "review"
        )

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(approve, range(2)))
    assert outcomes[0]["previews"] == outcomes[1]["previews"]
    assert len(store.get_messages(sid)) == 3
    assert store.get_qc_run(sid, rid)["revision"] == 2


def test_activation_during_approval_invalidates_old_version(registered):
    store, sid, rid, root, _, _ = registered
    run = store.get_qc_run(sid, rid)
    old = build_preview(run, root, Thresholds(**PARAMS))
    store.activate_qc_preview(sid, rid, 0, old)
    checked = store.get_qc_run(sid, rid)
    check_preview(checked, root, old)
    new = build_preview(checked, root, Thresholds(**PARAMS))
    store.activate_qc_preview(sid, rid, 1, new)
    with pytest.raises(ValueError, match="失效"):
        store.approve_qc_preview(sid, rid, checked["revision"], old["preview_id"], "review")
    assert len(store.get_messages(sid)) == 2


def test_untrusted_registration_fails_closed_and_history_omits_details(registered):
    store, sid, rid, root, _, _ = registered
    artifacts = {"metadata.json": str(root.parent / "fake" / "metadata.json")}
    agent = FakeAgent(
        runtime=FakeRuntime(
            default={
                "type": "analysis",
                "stage": "qc_review",
                "status": "needs_confirmation",
                "results": {"qc_artifacts": artifacts},
            }
        )
    )
    agent.workflow_manager = SimpleNamespace(
        workflow_recorder=SimpleNamespace(repo_root=root.parents[2])
    )
    result = run_turn(agent, store, sid, "QC", lambda _: True)
    assert result["status"] == "error"
    assert "qc_run_id" not in result["results"]
    assert len(store.get_qc_run(sid, rid)["previews"]) == 0
    run_turn(agent, store, sid, "下一问", lambda _: True)
    from src.control.agent_runtime import AgentRuntime

    messages = AgentRuntime(None, "unused", object())._build_messages(
        "下一问", agent.agent_runtime.calls[-1]["context"]
    )
    assert all(set(message) <= {"role", "content"} for message in messages)
    assert "metadata.json" not in str(messages)


def test_approval_failure_rolls_back_message_and_fact(registered):
    store, sid, rid, root, _, _ = registered
    run = store.get_qc_run(sid, rid)
    preview = build_preview(run, root, Thresholds(**PARAMS))
    store.activate_qc_preview(sid, rid, 0, preview)
    with store._conn() as conn:
        conn.execute(
            "CREATE TRIGGER fail_approval BEFORE UPDATE ON qc_previews "
            "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="test rollback"):
        store.approve_qc_preview(sid, rid, 1, preview["preview_id"], "review")
    assert len(store.get_messages(sid)) == 2
    assert store.get_qc_run(sid, rid)["previews"][0]["status"] == "pending"
    assert store.get_qc_run(sid, rid)["revision"] == 1


def test_parameter_binding_replacement_and_chat_approval_sequence(registered):
    store, sid, rid, root, _, _ = registered
    run = store.get_qc_run(sid, rid)
    preview = build_preview(run, root, Thresholds(**PARAMS))
    preview["params"]["percent_mt_max"] = "100"
    with pytest.raises(ValueError, match="绑定"):
        check_preview(run, root, preview)
    preview = build_preview(run, root, Thresholds(**PARAMS))
    store.activate_qc_preview(sid, rid, 0, preview)
    gate = Barrier(2)

    def append():
        gate.wait()
        SessionStore().append_message(sid, "user", "concurrent chat")

    def approve():
        gate.wait()
        SessionStore().approve_qc_preview(sid, rid, 1, preview["preview_id"], "review")

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda action: action(), [append, approve]))
    assert [m["seq"] for m in store.get_messages(sid)] == [1, 2, 3, 4]
