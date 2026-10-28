"""第五片只读证据、人工事实、CAS 与原子消息门禁。"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from tests.unit.test_sc_pca import PARAMS
from tests.unit.test_sc_pca import parent_ready as _parent_ready

parent_ready = _parent_ready


@pytest.fixture
def dimensions_ready(parent_ready):
    """复用第四片真实调用链替身产物，不新增分析夹具。"""
    store, sid, rid, root, source, client, url, pid, fid, path, binding, executor = parent_ready
    response = client.post(path, json={"binding_sha256": binding, "reason": "技术测试"})
    assert response.status_code == 200
    aid = response.json()["attempts"][0]["attempt_id"]
    executor.reset_mock()
    target = path + "/" + aid + "/dimensions"
    state = client.get(target).json()
    assert not state["blocked"] and state["evidence"]["actual_pcs"] == 2
    return SimpleNamespace(
        store=store,
        sid=sid,
        rid=rid,
        root=root,
        source=source,
        client=client,
        url=url,
        pid=pid,
        fid=fid,
        aid=aid,
        target=target,
        executor=executor,
        state=state,
        body={
            "binding_sha256": state["binding_sha256"],
            "revision": state["revision"],
            "n_dims": 1,
            "reason": "私有维度依据",
        },
    )


def test_read_record_restore_change_back_and_old_replay(dimensions_ready):
    """前缀边界、追加历史、无变化幂等，旧请求不重激活。"""
    d = dimensions_ready
    original = {p: p.read_bytes() for p in [d.source, *d.root.parent.rglob("*")] if p.is_file()}
    assert d.client.get(d.target).json() == d.state
    assert d.state["current"] is None and not d.state["history"]
    messages = len(d.store.get_messages(d.sid))
    first = d.client.post(d.target, json=d.body).json()
    assert first["current"]["dims"] == [1]
    assert first["revision"] == d.state["revision"] + 1
    assert d.client.post(d.target, json=d.body).json() == first
    same = {**d.body, "revision": first["revision"]}
    assert d.client.post(d.target, json=same).json() == first
    assert len(d.store.get_messages(d.sid)) == messages + 1
    second = d.client.post(d.target, json={**same, "n_dims": 2}).json()
    assert second["current"]["dims"] == [1, 2]
    assert d.client.post(d.target, json=d.body).json() == second
    assert d.client.post(d.target, json={**d.body, "n_dims": 2}).status_code == 409
    third = d.client.post(d.target, json={**d.body, "revision": second["revision"]}).json()
    assert third["current"]["dims"] == [1] and len(third["history"]) == 3
    assert third["history"][0]["invalidated_reason"]
    fourth = d.client.post(
        d.target, json={**d.body, "revision": third["revision"], "reason": "新依据"}
    ).json()
    assert len(fourth["history"]) == 4
    assert d.client.get(d.target).json() == fourth
    assert fourth["stage"] == "pca_review" and fourth["scientific_status"] == "unreviewed"
    assert all(p.read_bytes() == data for p, data in original.items())
    safe = json.dumps(d.store.get_messages(d.sid), ensure_ascii=False)
    assert "私有维度依据" not in safe and "新依据" not in safe
    assert "pca_dimensions" in safe and "pca_embeddings" not in safe
    d.executor.execute_script.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"n_dims": True},
        {"n_dims": 1.0},
        {"n_dims": "1"},
        {"n_dims": 0},
        {"n_dims": 3},
        {"n_dims": 10**100},
        {"revision": True},
        {"revision": -1},
        {"revision": 1.0},
        {"revision": 2**63},
        {"revision": 10**100},
        {"reason": " "},
        {"reason": "x" * 2001},
        {"script": "stop()"},
        {"dims": [1]},
        {"binding_sha256": "bad"},
    ],
)
def test_strict_request_rejected(dimensions_ready, payload):
    """信任边界不转 bool/小数，不接受任意脚本或集合。"""
    d = dimensions_ready
    assert d.client.post(d.target, json={**d.body, **payload}).status_code == 422
    assert not d.store.get_qc_run(d.sid, d.rid)["dimension_reviews"]
    d.executor.execute_script.assert_not_called()


def test_association_and_stale_binding_revision(dimensions_ready):
    """跨会话/父/PCA 404，陈旧绑定或 revision 409。"""
    d = dimensions_ready
    for target in (
        d.target.replace(d.sid, d.store.create_session()),
        d.target.replace(d.fid, "unknown"),
        d.target.replace(d.aid, "unknown"),
    ):
        assert d.client.get(target).status_code == 404
        assert d.client.post(target, json=d.body).status_code == 404
    for payload in (
        {"binding_sha256": "0" * 64},
        {"revision": d.body["revision"] - 1},
        {"revision": 2**63 - 1},
    ):
        assert d.client.post(d.target, json={**d.body, **payload}).status_code == 409


def test_database_write_failure_logged_without_exposing_details(
    dimensions_ready, monkeypatch, caplog
):
    """数据库故障记日志，响应不泄漏异常细节或创建半条记录。"""
    d = dimensions_ready
    before = len(d.store.get_messages(d.sid))

    def fail(*args, **kwargs):
        """模拟数据库写入失败。"""
        raise sqlite3.OperationalError("technical database failure")

    monkeypatch.setattr(type(d.store), "record_pca_dimensions", fail)
    response = d.client.post(d.target, json=d.body)
    assert response.status_code == 409
    assert "technical database failure" not in response.text
    assert "PCA dimension record database write failed" in caplog.text
    assert any(record.exc_info for record in caplog.records)
    assert d.body["reason"] not in caplog.text
    assert not d.store.get_qc_run(d.sid, d.rid)["dimension_reviews"]
    assert d.store.get_qc_run(d.sid, d.rid)["revision"] == d.body["revision"]
    assert len(d.store.get_messages(d.sid)) == before


@pytest.mark.parametrize(
    "change",
    [
        "input",
        "qc",
        "parent_rds",
        "metadata",
        "rds",
        "stdev",
        "script",
        "versions",
        "status",
        "tool_version",
        "protocol",
        "binding_sha256",
        "record_version",
        "running",
    ],
)
def test_invalid_evidence_preserves_history_and_blocks(dimensions_ready, change, monkeypatch):
    """文件、协议及状态变化后历史保留，不能继续记录。"""
    d = dimensions_ready
    saved = d.client.post(d.target, json=d.body).json()
    run = d.store.get_qc_run(d.sid, d.rid)
    directory = Path(run["pca_attempts"][0]["directory"])
    files = {
        "input": d.source,
        "qc": d.root / d.rid / "cell_qc.csv",
        "parent_rds": Path(run["attempts"][0]["directory"]) / "filtered_counts.rds",
        "metadata": directory / "metadata.json",
        "rds": directory / "seurat_pca.rds",
        "stdev": directory / "pca_stdev.csv",
        "script": directory / "script.R",
        "versions": directory / "versions.txt",
    }
    if change in files:
        files[change].write_bytes(files[change].read_bytes() + b"changed")
    elif change == "record_version":
        monkeypatch.setattr("src.api.routes_pca.DIMENSION_RECORD_VERSION", "future")
    else:
        column = "status" if change == "running" else change
        value = "running" if change == "running" else '"bad"' if change == "protocol" else "bad"
        with d.store._conn() as conn:
            conn.execute(
                f"UPDATE sc_pca_attempts SET {column} = ? WHERE attempt_id = ?", (value, d.aid)
            )
    state = d.client.get(d.target).json()
    assert state["current"] is None and len(state["history"]) == 1
    assert state["history"][0]["invalidated_reason"]
    assert (
        d.client.post(d.target, json={**d.body, "revision": saved["revision"]}).status_code == 409
    )
    d.executor.execute_script.assert_not_called()


def test_parent_restore_requires_new_explicit_record(dimensions_ready):
    """恢复旧父不自动复活旧记录或重发旧请求。"""
    d = dimensions_ready
    saved = d.client.post(d.target, json=d.body).json()
    assert d.client.post(d.url + "/preview", json=PARAMS).status_code == 200
    assert d.client.get(d.target).json()["current"] is None
    # 现有批准 API 不重新激活历史父；模拟未来恢复操作仍须递增 revision。
    with d.store._conn() as conn:
        conn.execute(
            "UPDATE qc_runs SET active_preview_id = ?, revision = revision + 1 WHERE run_id = ?",
            (d.pid, d.rid),
        )
    restored = d.client.get(d.target).json()
    assert restored["binding_sha256"] == saved["binding_sha256"]
    assert restored["current"] is None
    assert d.client.post(d.target, json=d.body).json()["current"] is None
    new = d.client.post(d.target, json={**d.body, "revision": restored["revision"]}).json()
    assert new["current"]["dims"] == [1] and len(new["history"]) == 2


def test_concurrent_different_records_only_one_wins(dimensions_ready):
    """多连接条件写入，冲突不静默覆盖。"""
    d = dimensions_ready
    barrier = Barrier(2)

    def post(k):
        barrier.wait()
        return d.client.post(d.target, json={**d.body, "n_dims": k})

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(post, [1, 2]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(d.store.get_qc_run(d.sid, d.rid)["dimension_reviews"]) == 1


def test_atomic_rollback_and_recheck_after_validation(dimensions_ready, monkeypatch):
    """消息/记录/revision 原子性，短事务重查运行阶段。"""
    d = dimensions_ready
    before = len(d.store.get_messages(d.sid))
    with d.store._conn() as conn:
        conn.execute(
            "CREATE TRIGGER reject_dimensions BEFORE INSERT ON sc_pca_dimension_reviews BEGIN SELECT RAISE(ABORT, 'forced rollback'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError, match="forced rollback"):
        d.client.post(d.target, json=d.body)
    assert len(d.store.get_messages(d.sid)) == before
    assert d.store.get_qc_run(d.sid, d.rid)["revision"] == d.body["revision"]
    with d.store._conn() as conn:
        conn.execute("DROP TRIGGER reject_dimensions")
    from src.api import routes_pca

    original = routes_pca.dimension_view

    def changed(*args):
        result = original(*args)
        with d.store._conn() as conn:
            conn.execute(
                "UPDATE sc_pca_attempts SET status = 'running' WHERE attempt_id = ?", (d.aid,)
            )
        return result

    monkeypatch.setattr(routes_pca, "dimension_view", changed)
    assert d.client.post(d.target, json=d.body).status_code == 409
    assert not d.store.get_qc_run(d.sid, d.rid)["dimension_reviews"]
