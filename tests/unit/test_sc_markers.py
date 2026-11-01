"""第七片公开 API 的复核事实、独立授权与证据失效。"""

import csv
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from src.analysis import sc_markers
from src.analysis.r_executor import RExecutorError
from src.ui import session_store
from tests.unit.test_qc_preview import PARAMS
from tests.unit.test_sc_cluster import dimensions_ready as _dimensions_ready
from tests.unit.test_sc_cluster import execute
from tests.unit.test_sc_cluster import parent_ready as _parent_ready
from tests.unit.test_sc_cluster import ready as _cluster_ready
from tests.unit.test_sc_pca import table

parent_ready = _parent_ready
dimensions_ready = _dimensions_ready
cluster_ready = _cluster_ready


@pytest.fixture
def reviewed_source(cluster_ready):
    """复用完整六片链路；本夹具尚未记录聚类复核。"""
    d = cluster_ready
    state = execute(d)
    d.cid = state["attempts"][0]["attempt_id"]
    d.review_path = f"{d.path}/{d.cid}/review"
    d.executor.reset_mock()
    return d


def test_review_is_local_append_only_fact_not_execution(reviewed_source):
    """查看、接受、恢复与相同请求重发都不运行 R 或泄漏依据到消息。"""
    d = reviewed_source
    state = d.client.get(d.review_path)
    assert state.status_code == 200, state.text
    state = state.json()
    assert state["current"] is None and not state["blocked"]
    body = {
        "binding_sha256": state["binding_sha256"],
        "revision": state["revision"],
        "accepted": True,
        "reason": "  私有探索性接受依据  ",
    }
    before = len(d.store.get_messages(d.sid))
    response = d.client.post(d.review_path, json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] == state["revision"] + 1
    assert saved["current"]["accepted"] is True
    assert saved["current"]["reason"] == body["reason"].strip()
    assert saved["stage"] == "cluster_review" and saved["scientific_status"] == "unreviewed"
    assert d.client.get(d.review_path).json() == saved
    assert d.client.post(d.review_path, json=body).json() == saved
    assert (
        d.client.post(d.review_path, json={**body, "revision": saved["revision"]}).json() == saved
    )
    assert d.client.post(d.review_path, json={**body, "reason": "不同依据"}).status_code == 409
    assert len(d.store.get_messages(d.sid)) == before + 1
    assert "私有探索性接受依据" not in json.dumps(d.store.get_messages(d.sid))
    assert not d.store.get_qc_run(d.sid, d.rid)["marker_attempts"]
    d.executor.execute_script.assert_not_called()


@pytest.fixture
def marker_ready(reviewed_source):
    """显式记录接受后只展示 marker 协议，不隐式执行。"""
    d = reviewed_source
    state = d.client.get(d.review_path).json()
    d.review_body = {
        "binding_sha256": state["binding_sha256"],
        "revision": state["revision"],
        "accepted": True,
        "reason": "探索性接受（仅本地）",
    }
    response = d.client.post(d.review_path, json=d.review_body)
    assert response.status_code == 200, response.text
    d.marker_path = f"{d.url}/markers/{d.fid}/{d.aid}/{d.cid}"
    state = d.client.get(d.marker_path).json()
    d.marker_body = {
        "binding_sha256": state["binding_sha256"],
        "cluster_review_id": state["review"]["review_id"],
        "revision": state["revision"],
        "reason": "私有 marker 授权依据",
    }
    return d


def marker_outputs(script, empty=False):
    """子进程边界的结构替身；真实数值和 RDS 完整性由集成测试证明。"""
    from src.analysis.sc_markers import MARKER_FIELDS, PLOT_FIELDS, PROTOCOL, SUMMARY_FIELDS

    directory = Path(script).parent
    code = Path(script).read_text(encoding="utf-8")
    cluster = Path(json.loads(code.split("cluster_dir <- ")[1].splitlines()[0]))
    pca = Path(json.loads(code.split("pca_dir <- ")[1].splitlines()[0]))
    mapping = list(csv.DictReader((pca / "feature_mapping.csv").open()))
    source_labels = list(csv.DictReader((cluster / "clusters.csv").open()))
    counts = {c: sum(r["cluster_id"] == c for r in source_labels) for c in ("0", "1")}
    m = mapping[1]
    marker_rows = (
        []
        if empty
        else [
            [
                "0",
                m["seurat_feature_key"],
                m["feature_id"],
                m["symbol"],
                0.0001,
                1,
                0.8,
                0.2,
                0.0003,
            ]
        ]
    )
    table(directory / "markers.csv", MARKER_FIELDS, marker_rows)
    summary_rows = [
        [
            c,
            counts[c],
            len(source_labels) - counts[c],
            "eligible",
            "no_returned_candidates" if empty or c == "1" else "candidates_returned",
            1 if c == "0" and not empty else 0,
            0,
        ]
        for c in ("0", "1")
    ]
    table(directory / "cluster_marker_summary.csv", SUMMARY_FIELDS, summary_rows)
    table(
        directory / "marker_dotplot_data.csv",
        PLOT_FIELDS,
        []
        if empty
        else [
            [c, m["seurat_feature_key"], m["feature_id"], m["symbol"], 0.5, 1, 0.6931471805599453]
            for c in ("0", "1")
        ],
    )
    (directory / "marker_dotplot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (directory / "versions.txt").write_text(
        "\n".join(f"{k} {v}" for k, v in PROTOCOL["packages"].items()) + "\n"
    )
    (directory / "session_info.txt").write_text("mock only")
    (directory / "summary.json").write_text(
        json.dumps(
            {
                "cells": len(source_labels),
                "features": len(mapping),
                "clusters": 2,
                "marker_rows": len(marker_rows),
                "positive_rows": len(marker_rows),
                "negative_rows": 0,
                "plot_features": [] if empty else [m["seurat_feature_key"]],
                "backend": PROTOCOL["backend"],
                "fastpath_succeeded": True,
                "presto_calls": 0 if empty else 1,
                "fallback_calls": 0,
                "presto_nthreads": [] if empty else [1],
                "source_object_unchanged": True,
                "seurat_threads": 1,
                "warnings": [],
            }
        )
    )
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def test_independent_marker_authorization_restores_own_snapshot_and_is_idempotent(marker_ready):
    d = marker_ready
    response = d.client.get(d.marker_path)
    assert response.status_code == 200, response.text
    state = response.json()
    assert not state["blocked"] and not state["attempts"] and state["binding_sha256"]
    d.executor.execute_script.assert_not_called()
    d.executor.execute_script.side_effect = marker_outputs
    body = {
        "binding_sha256": state["binding_sha256"],
        "cluster_review_id": state["review"]["review_id"],
        "revision": state["revision"],
        "reason": "私有 marker 授权依据",
    }
    originals = {p: p.read_bytes() for p in [d.source, *d.root.parent.rglob("*")] if p.is_file()}
    before = len(d.store.get_messages(d.sid))
    response = d.client.post(d.marker_path + "/execute", json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] == state["revision"] + 2
    assert saved["stage"] == "marker_review" and saved["scientific_status"] == "unreviewed"
    assert saved["attempts"][0]["status"] == "completed", saved
    assert saved["attempts"][0]["invalidated_reason"] is None
    assert saved["attempts"][0]["result"]["marker_rows"] == 1
    assert d.client.get(d.marker_path).json() == saved
    assert d.client.post(d.marker_path + "/execute", json=body).json() == saved
    aid = saved["attempts"][0]["attempt_id"]
    assert d.client.get(f"{d.marker_path}/{aid}/diagnostic").status_code == 200
    page = d.client.get(f"{d.marker_path}/{aid}/candidates?offset=0&limit=100")
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1 and page.json()["rows"][0]["symbol"] == "A_B"
    assert page.json()["rows"][0]["avg_log2FC"] == 1
    assert d.client.get(f"{d.marker_path}/{aid}/candidates?limit=101").status_code == 422
    d.executor.execute_script.assert_called_once()
    assert len(d.store.get_messages(d.sid)) == before + 1
    assert all(p.read_bytes() == data for p, data in originals.items())
    exposed = json.dumps(saved, ensure_ascii=False) + json.dumps(
        d.store.get_messages(d.sid), ensure_ascii=False
    )
    assert body["reason"] not in exposed and d.review_body["reason"] not in exposed
    assert "directory" not in exposed and str(d.source) not in exposed


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("revision", "1"),
        ("revision", -1),
        ("reason", "  "),
        ("reason", "x" * 2001),
        ("binding_sha256", "bad"),
        ("cluster_review_id", "bad"),
        ("max.cells.per.ident", 300),
    ],
)
def test_marker_strict_request_without_r(marker_ready, field, value):
    d = marker_ready
    assert (
        d.client.post(d.marker_path + "/execute", json={**d.marker_body, field: value}).status_code
        == 422
    )
    d.executor.execute_script.assert_not_called()


@pytest.mark.parametrize("accepted", [False, 1, "true", None])
def test_review_requires_strict_explicit_acceptance(reviewed_source, accepted):
    d = reviewed_source
    state = d.client.get(d.review_path).json()
    assert (
        d.client.post(
            d.review_path,
            json={
                "accepted": accepted,
                "revision": state["revision"],
                "binding_sha256": state["binding_sha256"],
                "reason": "x",
            },
        ).status_code
        == 422
    )
    d.executor.execute_script.assert_not_called()


def test_missing_review_and_wrong_associations_do_not_execute(reviewed_source):
    d = reviewed_source
    path = f"{d.url}/markers/{d.fid}/{d.aid}/{d.cid}"
    assert d.client.get(path).json()["blocked"]
    assert (
        d.client.post(
            path + "/execute",
            json={
                "binding_sha256": "0" * 64,
                "revision": 0,
                "cluster_review_id": "0" * 32,
                "reason": "x",
            },
        ).status_code
        == 404
    )
    other = d.store.create_session()
    assert d.client.get(path.replace(d.sid, other)).status_code == 404
    assert d.client.get(path.replace(d.cid, "0" * 32)).status_code == 404
    d.executor.execute_script.assert_not_called()


def test_stale_revision_and_binding_conflict(marker_ready):
    d = marker_ready
    for field, value, status in [
        ("revision", d.marker_body["revision"] - 1, 409),
        ("binding_sha256", "0" * 64, 409),
        ("cluster_review_id", "0" * 32, 404),
    ]:
        assert (
            d.client.post(
                d.marker_path + "/execute", json={**d.marker_body, field: value}
            ).status_code
            == status
        )
    d.executor.execute_script.assert_not_called()


def test_running_guards_and_duplicate_request(marker_ready):
    d = marker_ready
    entered, release = Event(), Event()

    def waiting(script):
        entered.set()
        assert release.wait(30)
        return marker_outputs(script)

    d.executor.execute_script.side_effect = waiting
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(d.client.post, d.marker_path + "/execute", json=d.marker_body)
        assert entered.wait(20)
        try:
            assert d.client.get(d.marker_path).json()["attempts"][0]["status"] == "running"
            assert d.client.post(d.marker_path + "/execute", json=d.marker_body).status_code == 200
            assert d.client.get(d.url).json()["marker_running"]
            assert d.client.post(d.url + "/preview", json=PARAMS).status_code == 409
            assert (
                d.client.post(
                    d.url + "/approve", json={"preview_id": d.pid, "reason": "x"}
                ).status_code
                == 409
            )
            assert d.client.get(d.target).json()["blocked"]
            review = d.client.get(d.review_path).json()
            assert review["current"] is None and review["blocked"]
            assert (
                d.client.post(
                    d.review_path, json={**d.review_body, "revision": review["revision"]}
                ).status_code
                == 409
            )
            assert d.client.delete(f"/api/sessions/{d.sid}").status_code == 409
            with pytest.raises(ValueError, match="marker"):
                d.store.delete_session(d.sid)
        finally:
            release.set()
        assert future.result().json()["attempts"][0]["status"] == "completed"
    d.executor.execute_script.assert_called_once()


@pytest.mark.parametrize("mutation", ["revision", "review", "cluster", "attempt", "source"])
def test_running_identity_mutation_is_failed_not_completed(marker_ready, mutation):
    d = marker_ready

    def corrupt(script):
        result = marker_outputs(script)
        with d.store._conn() as conn:
            if mutation == "revision":
                conn.execute(
                    "UPDATE qc_runs SET revision = revision + 10 WHERE run_id = ?", (d.rid,)
                )
            elif mutation == "review":
                conn.execute(
                    "UPDATE sc_cluster_reviews SET reason = 'changed' WHERE cluster_attempt_id = ?",
                    (d.cid,),
                )
            elif mutation == "cluster":
                conn.execute(
                    "UPDATE sc_cluster_attempts SET reason = 'changed' WHERE attempt_id = ?",
                    (d.cid,),
                )
            elif mutation == "attempt":
                conn.execute(
                    "UPDATE sc_marker_attempts SET reason = 'changed' WHERE run_id = ?", (d.rid,)
                )
            else:
                d.source.write_bytes(b"changed")
        return result

    d.executor.execute_script.side_effect = corrupt
    response = d.client.post(d.marker_path + "/execute", json=d.marker_body)
    assert response.status_code == 200, response.text
    assert response.json()["attempts"][0]["status"] == "failed"
    assert response.json()["revision"] == d.marker_body["revision"] + (
        12 if mutation == "revision" else 2
    )
    assert d.client.post(d.marker_path + "/execute", json=d.marker_body).status_code == 200
    d.executor.execute_script.assert_called_once()


@pytest.mark.parametrize(
    "kind", ["missing", "nan", "mapping", "duplicate", "partial", "fallback", "version"]
)
def test_invalid_marker_outputs_fail_and_never_retry(marker_ready, kind):
    d = marker_ready

    def corrupt(script):
        result = marker_outputs(script)
        directory = Path(script).parent
        if kind == "missing":
            (directory / "marker_dotplot.png").unlink()
        elif kind in ("nan", "mapping", "duplicate"):
            path = directory / "markers.csv"
            rows = list(csv.reader(path.open()))
            if kind == "nan":
                rows[1][5] = "NaN"
            elif kind == "mapping":
                rows[1][2] = "wrong-feature"
            else:
                rows.append(rows[1])
            table(path, rows[0], rows[1:])
        elif kind == "partial":
            path = directory / "cluster_marker_summary.csv"
            rows = list(csv.reader(path.open()))
            table(path, rows[0], rows[1:2])
        elif kind == "version":
            (directory / "versions.txt").write_text("presto wrong")
        else:
            path = directory / "summary.json"
            summary = json.loads(path.read_text())
            summary["fallback_calls"] = 1
            path.write_text(json.dumps(summary))
        return result

    d.executor.execute_script.side_effect = corrupt
    state = d.client.post(d.marker_path + "/execute", json=d.marker_body).json()
    assert state["attempts"][0]["status"] == "failed", state
    assert d.client.post(d.marker_path + "/execute", json=d.marker_body).json() == state
    d.executor.execute_script.assert_called_once()


def test_legal_zero_markers_are_completed_with_all_cluster_statuses(marker_ready):
    d = marker_ready
    d.executor.execute_script.side_effect = lambda script: marker_outputs(script, empty=True)
    state = d.client.post(d.marker_path + "/execute", json=d.marker_body).json()
    attempt = state["attempts"][0]
    assert attempt["status"] == "completed", state
    assert attempt["result"]["marker_rows"] == 0
    assert len(attempt["result"]["cluster_summary"]) == 2


def test_marker_protocol_drives_generated_script_and_validation(marker_ready, monkeypatch):
    """协议改变时脚本和数值门禁必须随之改变，不复制固定阈值。"""
    d = marker_ready
    d.executor.execute_script.side_effect = marker_outputs
    state = d.client.post(d.marker_path + "/execute", json=d.marker_body).json()
    assert state["attempts"][0]["status"] == "completed"
    run = d.store.get_qc_run(d.sid, d.rid)
    attempt = run["marker_attempts"][0]
    directory = Path(attempt["directory"])
    cluster_directory = Path(attempt["snapshot"]["cluster"]["directory"])
    pca_directory = Path(run["pca_attempts"][0]["directory"])
    monkeypatch.setitem(sc_markers.PROTOCOL["plot"], "positive_top_per_cluster", 3)
    monkeypatch.setitem(sc_markers.PROTOCOL["markers"], "random.seed", 17)
    script = sc_markers.marker_script(cluster_directory, pca_directory, directory)
    assert "set.seed(17)" in script and "random.seed=17" in script
    assert "head(out$feature_key[out$cluster_id == g & out$avg_log2FC > 0], 3L)" in script
    for field, value in [("min.pct", 0.9), ("logfc.threshold", 2), ("return.thresh", 0.00001)]:
        with monkeypatch.context() as scoped:
            scoped.setitem(sc_markers.PROTOCOL["markers"], field, value)
            with pytest.raises(ValueError, match="统计阈值"):
                sc_markers.check_outputs(directory, pca_directory, attempt["snapshot"]["source"])
    monkeypatch.setitem(sc_markers.PROTOCOL["plot"], "positive_top_per_cluster", 0)
    table(directory / "marker_dotplot_data.csv", sc_markers.PLOT_FIELDS, [])
    summary = json.loads((directory / "summary.json").read_bytes())
    summary["plot_features"] = []
    (directory / "summary.json").write_text(json.dumps(summary))
    checked = sc_markers.check_outputs(directory, pca_directory, attempt["snapshot"]["source"])
    assert checked["plot_features"] == []


def test_old_marker_tool_request_never_runs_new_tool_implicitly(marker_ready, monkeypatch):
    """工具升级使旧产物成为历史；旧授权不能隐式运行新版。"""
    d = marker_ready
    d.executor.execute_script.side_effect = marker_outputs
    response = d.client.post(d.marker_path + "/execute", json=d.marker_body)
    assert response.json()["attempts"][0]["status"] == "completed"
    monkeypatch.setattr(sc_markers, "MARKER_TOOL_VERSION", "test-upgrade")
    monkeypatch.setattr("src.api.routes_markers.MARKER_TOOL_VERSION", "test-upgrade")
    history = d.client.get(d.marker_path).json()["attempts"][0]
    assert history["invalidated_reason"] and history["result"] is None
    assert d.client.post(d.marker_path + "/execute", json=d.marker_body).status_code == 409
    assert len(d.store.get_qc_run(d.sid, d.rid)["marker_attempts"]) == 1
    d.executor.execute_script.assert_called_once()


@pytest.mark.parametrize("sizes", [[31], [2, 29], [29, 2]])
def test_ineligible_cluster_groups_block_before_r(marker_ready, monkeypatch, sizes):
    """已校验上游汇总进入 marker 资格边界时，单簇或小组不得补救。"""
    d = marker_ready
    checked = sc_markers.checked_completed_cluster

    def summary(*args):
        result = checked(*args)
        return {
            **result,
            "clusters": len(sizes),
            "cluster_sizes": [
                {"cluster_id": str(i), "cells": cells} for i, cells in enumerate(sizes)
            ],
        }

    monkeypatch.setattr(sc_markers, "checked_completed_cluster", summary)
    state = d.client.get(d.marker_path).json()
    assert state["binding_sha256"] is None
    assert "不会合并小簇" in state["blocked"][0]
    assert d.client.post(d.marker_path + "/execute", json=d.marker_body).status_code == 409
    assert not d.store.get_qc_run(d.sid, d.rid)["marker_attempts"]
    d.executor.execute_script.assert_not_called()


@pytest.mark.parametrize("kind", ["error", "timeout", "nonzero"])
def test_r_failure_keeps_local_evidence_without_retry_or_private_message(marker_ready, kind):
    """R 非零、具体异常与有部分日志的超时均收口失败，不重复执行。"""
    d = marker_ready

    def fail(script):
        if kind == "nonzero":
            return SimpleNamespace(returncode=1, stdout="private stdout", stderr="private stderr")
        if kind == "timeout":
            try:
                raise subprocess.TimeoutExpired(
                    "Rscript", 300, output=b"private stdout", stderr=b"private stderr"
                )
            except subprocess.TimeoutExpired as cause:
                raise RExecutorError("R script timed out") from cause
        raise RExecutorError("private failure", "private stdout", "private stderr")

    d.executor.execute_script.side_effect = fail
    response = d.client.post(d.marker_path + "/execute", json=d.marker_body)
    assert response.status_code == 200, response.text
    state = response.json()
    attempt = state["attempts"][0]
    assert attempt["status"] == "failed" and attempt["result"] is None
    assert state["revision"] == d.marker_body["revision"] + 2
    assert "private" not in json.dumps(d.store.get_messages(d.sid))
    saved = d.store.get_qc_run(d.sid, d.rid)["marker_attempts"][0]
    directory = Path(saved["directory"])
    assert "private stdout" in (directory / "stdout.txt").read_text()
    assert "private stderr" in (directory / "stderr.txt").read_text()
    assert d.client.post(d.marker_path + "/execute", json=d.marker_body).json() == state
    assert d.client.get(f"{d.marker_path}/{attempt['attempt_id']}/candidates").status_code == 404
    d.executor.execute_script.assert_called_once()


@pytest.mark.parametrize("kind", ["artifact", "protocol", "review"])
def test_history_drift_invalidates_diagnostic(marker_ready, kind):
    d = marker_ready
    d.executor.execute_script.side_effect = marker_outputs
    state = d.client.post(d.marker_path + "/execute", json=d.marker_body).json()
    aid = state["attempts"][0]["attempt_id"]
    if kind == "artifact":
        attempt = d.store.get_qc_run(d.sid, d.rid)["marker_attempts"][0]
        (Path(attempt["directory"]) / "markers.csv").write_text("changed")
    else:
        with d.store._conn() as conn:
            if kind == "protocol":
                conn.execute(
                    "UPDATE sc_marker_attempts SET tool_version = 'changed' WHERE attempt_id = ?",
                    (aid,),
                )
            else:
                conn.execute(
                    "UPDATE sc_cluster_reviews SET reason = 'changed' WHERE cluster_attempt_id = ?",
                    (d.cid,),
                )
    history = d.client.get(d.marker_path).json()["attempts"][0]
    assert history["invalidated_reason"] and history["result"] is None
    assert d.client.get(f"{d.marker_path}/{aid}/diagnostic").status_code == 409
    assert d.client.get(f"{d.marker_path}/{aid}/candidates").status_code == 409


def test_terminal_message_failure_rolls_back_and_restart_never_resumes(marker_ready, monkeypatch):
    d = marker_ready
    d.executor.execute_script.side_effect = marker_outputs
    before = len(d.store.get_messages(d.sid))

    def fail(_):
        raise sqlite3.OperationalError("private encoding failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(session_store, "_encode_results", fail)
        response = d.client.post(d.marker_path + "/execute", json=d.marker_body)
    assert response.status_code == 409 and "private encoding failure" not in response.text
    run = d.store.get_qc_run(d.sid, d.rid)
    assert run["marker_attempts"][0]["status"] == "running"
    assert run["revision"] == d.marker_body["revision"] + 1
    assert len(d.store.get_messages(d.sid)) == before
    d.store.interrupt_sc_markers()
    state = d.client.get(d.marker_path).json()
    assert state["attempts"][0]["status"] == "interrupted"
    assert state["revision"] == d.marker_body["revision"] + 2
    assert len(d.store.get_messages(d.sid)) == before + 1
    d.store.interrupt_sc_markers()
    assert len(d.store.get_messages(d.sid)) == before + 1
    assert d.client.post(d.marker_path + "/execute", json=d.marker_body).json() == state
    d.executor.execute_script.assert_called_once()
