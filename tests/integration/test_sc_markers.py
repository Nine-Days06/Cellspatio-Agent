"""第七片真实 R：完整 API、全细胞 Presto、独立重复与源对象不变。"""

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from fastapi.testclient import TestClient

from src.analysis.r_executor import RExecutor
from src.analysis.sc_markers import check_outputs, marker_script
from src.api.app import create_app
from src.ui.session_store import SessionStore
from tests.integration.test_sc_pca import setup_parent
from tests.unit.fakes import FakeAgent


def test_real_marker_preserves_numeric_feature_ids(tmp_path):
    """固定 R 与 Python 验证都必须逐字保留原数字 ID 的前导零。"""
    rng = np.random.default_rng(20261029)
    means = np.ones((150, 90))
    for group in range(3):
        means[group * 40 : (group + 1) * 40, group * 30 : (group + 1) * 30] = 25
    np.savetxt(tmp_path / "counts.csv", rng.poisson(means), delimiter=",", fmt="%d")
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    executor = RExecutor()
    executor.execute_code(f"""
        suppressPackageStartupMessages(library(Seurat))
        counts <- as.matrix(read.csv({json.dumps((tmp_path / "counts.csv").as_posix())}, header=FALSE))
        keys <- sprintf("F%08d", seq_len(nrow(counts)))
        rownames(counts) <- keys; colnames(counts) <- paste0("c", seq_len(ncol(counts)))
        obj <- CreateSeuratObject(counts=counts)
        obj <- NormalizeData(obj, verbose=FALSE)
        Idents(obj) <- rep(c("0", "1", "2"), each=30)
        saveRDS(obj, {json.dumps((source / "seurat_clustered.rds").as_posix())})
        write.csv(data.frame(barcode=colnames(obj), cluster_id=as.character(Idents(obj))),
                  {json.dumps((source / "clusters.csv").as_posix())}, row.names=FALSE)
        write.csv(data.frame(seurat_feature_key=keys, feature_id=sprintf("%07d", seq_along(keys)),
                             symbol=paste0("original_", seq_along(keys))),
                  {json.dumps((source / "feature_mapping.csv").as_posix())}, row.names=FALSE)
    """)
    originals = {p: p.read_bytes() for p in source.iterdir()}
    script = output / "script.R"
    script.write_text(marker_script(source, source, output), encoding="utf-8")
    executor.execute_script(str(script))
    summary = check_outputs(
        output,
        source,
        {"cells": 90, "cluster_sizes": [{"cluster_id": str(i), "cells": 30} for i in range(3)]},
    )
    assert summary["marker_rows"] > 0 and summary["plot_features"]
    for name in ("markers.csv", "marker_dotplot_data.csv"):
        with (output / name).open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows
        assert all(r["feature_id"] == f"{int(r['feature_key'][1:]):07d}" for r in rows)
    assert all(p.read_bytes() == before for p, before in originals.items())


def test_real_marker_roundtrip_repeat_and_presto_failure(tmp_path, monkeypatch):
    """三组信号仅用于技术验收；重复不代表生物学复现或跨版本 bitwise。"""
    rng = np.random.default_rng(20261029)
    means = np.ones((150, 90))
    for group in range(3):
        means[group * 40 : (group + 1) * 40, group * 30 : (group + 1) * 30] = 25
    counts = rng.poisson(means)
    executor, root, run, parent = setup_parent(tmp_path, counts)
    store = SessionStore()
    sid = store.create_session()
    rid = run["run_id"]
    store.register_qc_run(sid, rid, run["directory"], run["evidence"])
    preview = run["previews"][0]
    store.activate_qc_preview(sid, rid, 0, preview)
    store.approve_qc_preview(sid, rid, 1, preview["preview_id"], "技术测试")
    store.start_sc_filter(
        sid,
        rid,
        2,
        preview["preview_id"],
        parent["binding_sha256"],
        parent["tool_version"],
        parent["directory"],
        parent["attempt_id"],
    )
    store.finish_sc_filter(parent["attempt_id"], parent["result"])
    agent = FakeAgent()
    agent.workflow_manager = SimpleNamespace(
        workflow_recorder=SimpleNamespace(repo_root=tmp_path), r_executor=executor, kg_memory=Mock()
    )
    agent.llm_client = Mock()
    monkeypatch.setenv("OLLAMA_EAGER_START", "0")
    client = TestClient(create_app(agent))
    base = f"/api/sessions/{sid}/qc/{rid}"
    pca_path = f"{base}/pca/{parent['attempt_id']}"
    state = client.get(pca_path).json()
    response = client.post(
        pca_path, json={"binding_sha256": state["binding_sha256"], "reason": "技术测试"}
    )
    assert response.status_code == 200, response.text
    pca = response.json()["attempts"][0]
    assert pca["status"] == "completed", pca
    dims_path = f"{pca_path}/{pca['attempt_id']}/dimensions"
    dims = client.get(dims_path).json()
    response = client.post(
        dims_path,
        json={
            "binding_sha256": dims["binding_sha256"],
            "revision": dims["revision"],
            "n_dims": 5,
            "reason": "技术前缀",
        },
    )
    assert response.status_code == 200, response.text
    cluster_path = f"{base}/clusters/{parent['attempt_id']}/{pca['attempt_id']}"
    state = client.get(cluster_path).json()
    response = client.post(
        cluster_path + "/execute",
        json={
            "binding_sha256": state["binding_sha256"],
            "dimension_review_id": state["selection"]["review_id"],
            "revision": state["revision"],
            "reason": "技术聚类",
        },
    )
    assert response.status_code == 200, response.text
    cluster = response.json()["attempts"][0]
    assert cluster["status"] == "completed", cluster
    assert cluster["result"]["clusters"] >= 2
    review_path = f"{cluster_path}/{cluster['attempt_id']}/review"
    marker_path = (
        f"{base}/markers/{parent['attempt_id']}/{pca['attempt_id']}/{cluster['attempt_id']}"
    )
    originals = {
        p: p.read_bytes()
        for p in [Path(run["evidence"]["input_path"]), *root.parent.rglob("*")]
        if p.is_file()
    }
    directories = []
    for repeat in range(2):
        review = client.get(review_path).json()
        response = client.post(
            review_path,
            json={
                "binding_sha256": review["binding_sha256"],
                "revision": review["revision"],
                "accepted": True,
                "reason": f"独立探索性技术接受 {repeat}",
            },
        )
        assert response.status_code == 200, response.text
        state = client.get(marker_path).json()
        assert not state["blocked"] and state["binding_sha256"], state
        body = {
            "binding_sha256": state["binding_sha256"],
            "revision": state["revision"],
            "cluster_review_id": state["review"]["review_id"],
            "reason": f"独立 marker 技术授权 {repeat}",
        }
        response = client.post(marker_path + "/execute", json=body)
        assert response.status_code == 200, response.text
        saved = response.json()
        attempt = store.get_qc_run(sid, rid)["marker_attempts"][-1]
        assert attempt["status"] == "completed", attempt["result"]
        assert saved["attempts"][-1]["invalidated_reason"] is None
        assert saved["stage"] == "marker_review" and saved["scientific_status"] == "unreviewed"
        assert client.post(marker_path + "/execute", json=body).json() == saved
        assert client.get(f"{marker_path}/{attempt['attempt_id']}/diagnostic").status_code == 200
        result = attempt["result"]
        assert result["presto_calls"] == 1 and result["fallback_calls"] == 0
        assert result["presto_nthreads"] == [1] and result["source_object_unchanged"] is True
        assert result["positive_rows"] > 0 and result["negative_rows"] > 0
        directories.append(Path(attempt["directory"]))
    assert all(p.read_bytes() == value for p, value in originals.items())
    for name in ("markers.csv", "cluster_marker_summary.csv", "marker_dotplot_data.csv"):
        assert (directories[0] / name).read_bytes() == (directories[1] / name).read_bytes()
    rows = list(csv.DictReader((directories[0] / "markers.csv").open()))
    assert any("_" in r["symbol"] or "|" in r["symbol"] for r in rows)
    agent.llm_client.assert_not_called()
    assert not agent.workflow_manager.kg_memory.mock_calls

    # 真实 Presto 失败不得被 Seurat 吞掉后转逐簇后备；只改独立探针脚本。
    probe = tmp_path / "presto_failure_probe"
    probe.mkdir()
    code = (directories[0] / "script.R").read_text(encoding="utf-8")
    code = code.replace(json.dumps(directories[0].as_posix()), json.dumps(probe.as_posix()))
    code = code.replace(
        "guard$presto_calls <- guard$presto_calls + 1L", 'stop("injected presto failure")'
    )
    import pytest

    from src.analysis.r_executor import RExecutorError

    with pytest.raises(RExecutorError, match="required presto fastpath failed"):
        executor.execute_code(code)
    assert not (probe / "summary.json").exists()
