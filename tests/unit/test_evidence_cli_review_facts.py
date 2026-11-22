"""CLI 复核公开入口：真实文件与临时 SQLite，复用既有 R 适配夹具。"""

import json
import sqlite3
from pathlib import Path

import pytest

from src.cli import evidence
from src.evidence.builder import build_cluster_card, build_marker_card, build_qc_card
from tests.unit.test_sc_markers import cluster_ready as _cluster_ready
from tests.unit.test_sc_markers import dimensions_ready as _dimensions_ready
from tests.unit.test_sc_markers import marker_outputs
from tests.unit.test_sc_markers import marker_ready as _marker_ready
from tests.unit.test_sc_markers import parent_ready as _parent_ready
from tests.unit.test_sc_markers import reviewed_source as _reviewed_source

parent_ready = _parent_ready
dimensions_ready = _dimensions_ready
cluster_ready = _cluster_ready
reviewed_source = _reviewed_source
marker_ready = _marker_ready


def cli_base(store, monkeypatch):
    """把真实库备份到 CLI 约定位置，页面与 CLI 使用同一临时数据库。"""
    base = store.db_path.parent / "cli"
    database = base / ".cellspatio" / "sessions.db"
    database.parent.mkdir(parents=True)
    with store._conn() as source, sqlite3.connect(database) as target:
        source.backup(target)
    store.db_path = database
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(database))
    return base


def review(kind, d, target, base, *, reason="私有复核依据", decision=None):
    """从公开 CLI 参数执行复核，不绕过命令路由。"""
    args = ["review", kind, d.sid, d.rid, target, "--reason", reason, "--root", str(base)]
    if decision:
        args.extend(["--decision", decision])
    return evidence.main(args)


def test_cli_qc_rejects_drift_but_allows_quality_rejection(reviewed_source, capsys, monkeypatch):
    """科学零保留可拒绝；漂移不得新增，已有拒绝重发不恢复当前。"""
    d = reviewed_source
    base = cli_base(d.store, monkeypatch)
    response = d.client.post(
        d.url + "/preview",
        json={"n_feature_min": 4, "n_feature_max": 4, "percent_mt_max": 100},
    )
    preview = response.json()["active_preview"]
    assert preview["summary"]["blocked"]
    target = preview["preview_id"]
    selection = d.root / d.rid / "previews" / target / "selection.csv"
    original = selection.read_bytes()
    selection.write_bytes(original + b"changed")
    assert review("qc", d, target, base) == 2
    assert d.store.get_qc_run(d.sid, d.rid).active_preview["status"] == "pending"
    capsys.readouterr()
    selection.write_bytes(original)
    assert review("qc", d, target, base) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "ok",
        "kind": "qc",
        "target_id": target,
    }
    assert build_qc_card(d.store, d.sid, d.rid).outcome == "rejected"
    count = len(d.store.get_messages(d.sid))
    (d.root / d.rid / "qc.png").unlink()
    assert review("qc", d, target, base) == 0
    assert len(d.store.get_messages(d.sid)) == count
    assert d.client.get(d.url).json()["blocked"]
    with pytest.raises(ValueError, match="复核已失效"):
        build_qc_card(d.store, d.sid, d.rid)
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_cli_cluster_rejection_preserves_only_identical_history(reviewed_source, monkeypatch):
    """拒绝也验证真实聚类；失效后仅重发原历史，不能产生新事实。"""
    d = reviewed_source
    base = cli_base(d.store, monkeypatch)
    attempt = d.store.get_qc_run(d.sid, d.rid).attempt("cluster", d.cid)
    path = Path(attempt["directory"]) / "clusters.csv"
    original = path.read_bytes()
    path.write_bytes(original + b"changed")
    assert review("cluster", d, d.cid, base) == 2
    assert not d.store.get_qc_run(d.sid, d.rid).cluster_rejections()
    path.write_bytes(original)
    assert review("cluster", d, d.cid, base) == 0
    assert build_cluster_card(d.store, d.sid, d.rid).outcome == "rejected"
    count = len(d.store.get_messages(d.sid))
    path.unlink()
    assert review("cluster", d, d.cid, base) == 0
    assert review("cluster", d, d.cid, base, reason="新的复核依据") == 2
    assert len(d.store.get_messages(d.sid)) == count
    with pytest.raises(ValueError, match="复核已失效"):
        build_cluster_card(d.store, d.sid, d.rid)


@pytest.mark.parametrize("decision", ["accepted", "rejected"])
def test_cli_marker_review_requires_real_result_and_keeps_history(
    marker_ready, decision, monkeypatch
):
    """接受与拒绝使用同一真实结果，漂移只保留原人工历史。"""
    d = marker_ready
    d.executor.execute_script.side_effect = marker_outputs
    state = d.client.post(d.marker_path + "/execute", json=d.marker_body).json()
    target = state["attempts"][0]["attempt_id"]
    d.executor.reset_mock()
    base = cli_base(d.store, monkeypatch)
    attempt = d.store.get_qc_run(d.sid, d.rid).attempt("marker", target)
    path = Path(attempt["directory"]) / "markers.csv"
    original = path.read_bytes()
    path.write_bytes(original + b"changed")
    assert review("marker", d, target, base, decision=decision) == 2
    assert not d.store.get_qc_run(d.sid, d.rid).marker_reviews()
    path.write_bytes(original)
    assert review("marker", d, target, base, decision=decision) == 0
    assert build_marker_card(d.store, d.sid, d.rid).outcome == decision
    count = len(d.store.get_messages(d.sid))
    path.unlink()
    assert review("marker", d, target, base, decision=decision) == 0
    assert review("marker", d, target, base, decision=decision, reason="新依据") == 2
    assert len(d.store.get_messages(d.sid)) == count
    with pytest.raises(ValueError, match="复核已失效"):
        build_marker_card(d.store, d.sid, d.rid)
    d.executor.execute_script.assert_not_called()
