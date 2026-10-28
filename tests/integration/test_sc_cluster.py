"""第六片真实 R：完整 API、对象不变、稀疏图/标签/坐标与同安装重复。"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.ui.session_store import SessionStore
from tests.integration.test_sc_pca import setup_parent
from tests.unit.fakes import FakeAgent


def test_real_cluster_api_roundtrip_and_repeat(tmp_path, monkeypatch):
    """重复同一固定协议不承诺跨版本 bitwise；两次独立明确授权。"""
    rng = np.random.default_rng(20261028)
    counts = rng.poisson(np.linspace(1, 25, 80)[:, None] * rng.uniform(0.2, 2, (80, 40)))
    counts[4] = 0
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
        workflow_recorder=SimpleNamespace(repo_root=tmp_path), r_executor=executor
    )
    agent.llm_client = Mock()
    agent.workflow_manager.kg_memory = Mock()
    monkeypatch.setenv("OLLAMA_EAGER_START", "0")
    client = TestClient(create_app(agent))
    url = f"/api/sessions/{sid}/qc/{rid}/pca/{parent['attempt_id']}"
    state = client.get(url).json()
    response = client.post(
        url, json={"binding_sha256": state["binding_sha256"], "reason": "技术测试"}
    )
    assert response.status_code == 200, response.text
    pca = response.json()["attempts"][0]
    assert pca["status"] == "completed", pca
    dims_path = f"{url}/{pca['attempt_id']}/dimensions"
    cluster_path = (
        f"/api/sessions/{sid}/qc/{rid}/clusters/{parent['attempt_id']}/{pca['attempt_id']}"
    )
    originals = {
        p: p.read_bytes()
        for p in [Path(run["evidence"]["input_path"]), *root.parent.rglob("*")]
        if p.is_file()
    }
    directories = []
    for repeat in range(2):
        dims = client.get(dims_path).json()
        response = client.post(
            dims_path,
            json={
                "binding_sha256": dims["binding_sha256"],
                "revision": dims["revision"],
                "n_dims": 5,
                "reason": f"独立技术前缀试验 {repeat}",
            },
        )
        assert response.status_code == 200, response.text
        state = client.get(cluster_path).json()
        assert not state["blocked"], state
        response = client.post(
            cluster_path + "/execute",
            json={
                "binding_sha256": state["binding_sha256"],
                "dimension_review_id": state["selection"]["review_id"],
                "revision": state["revision"],
                "reason": f"独立第六片试验 {repeat}",
            },
        )
        assert response.status_code == 200, response.text
        attempt = store.get_qc_run(sid, rid)["cluster_attempts"][-1]
        assert attempt["status"] == "completed", attempt["result"]
        directory = Path(attempt["directory"])
        directories.append(directory)
        assert client.get(cluster_path).json()["attempts"][-1]["invalidated_reason"] is None
        assert client.get(f"{cluster_path}/{pca['attempt_id']}/diagnostic").status_code == 404
        assert client.get(f"{cluster_path}/{attempt['attempt_id']}/diagnostic").status_code == 200
        assert attempt["result"]["n_epochs"] == 500
        assert attempt["result"]["seurat_threads"] == 1
        assert sum(r["cells"] for r in attempt["result"]["cluster_sizes"]) == 40
    assert all(p.read_bytes() == value for p, value in originals.items())
    agent.llm_client.assert_not_called()
    assert not agent.workflow_manager.kg_memory.mock_calls
    # 独立进程检查两次 partition / 稀疏图 / 坐标，保留真实证据。
    pca_dir = Path(store.get_qc_run(sid, rid)["pca_attempts"][0]["directory"])
    script = f"""
    suppressPackageStartupMessages(library(Seurat))
    suppressPackageStartupMessages(library(Matrix))
    p <- readRDS({json.dumps((pca_dir / "seurat_pca.rds").as_posix())})
    a <- readRDS({json.dumps((directories[0] / "seurat_clustered.rds").as_posix())})
    b <- readRDS({json.dumps((directories[1] / "seurat_clustered.rds").as_posix())})
    for (x in list(a,b)) {{
      for (layer in c("counts", "data", "scale.data")) stopifnot(identical(
          LayerData(x, layer=layer), LayerData(p, layer=layer)))
      stopifnot(identical(x[["pca"]], p[["pca"]]), identical(VariableFeatures(x), VariableFeatures(p)),
                identical(x@misc$feature_mapping, p@misc$feature_mapping),
                identical(colnames(x), colnames(p)), isSymmetric(x[["RNA_snn"]]),
                all(is.finite(Embeddings(x,"umap"))), ncol(Embeddings(x,"umap"))==2L)
      for (g in x@graphs) stopifnot(inherits(g,"sparseMatrix"), all(g@x>=0), all(is.finite(g@x)))
    }}
    partition <- function(x) outer(as.character(Idents(x)),as.character(Idents(x)), "==")
    stopifnot(identical(partition(a),partition(b)), identical(a@graphs,b@graphs),
              isTRUE(all.equal(Embeddings(a,"umap"),Embeddings(b,"umap"),tolerance=1e-10)))
    """
    result = executor.execute_code(script)
    assert result.returncode == 0, result.stderr
