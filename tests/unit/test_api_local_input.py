"""POST /api/sessions/{id}/local-input：无 LLM 的本地输入登记与 QC 关联。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.analysis.sc_qc import run_sc_qc
from src.api.app import create_app
from src.api.chat_service import session_lock
from src.control.workflow_manager import WorkflowManager
from src.ui.session_store import SessionStore
from tests.unit.fakes import QC_HEADER, FakeAgent, fake_qc_executor


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "counts.csv"
    path.write_text("gene,c1,c2\nMT-X,1,1\nG1,0,3\n", encoding="utf-8")
    return path


def _qc_result(tmp_path, source: Path) -> dict:
    """真实 run_sc_qc 证据链；只有 R 产物用微型数据替代。"""
    executor = fake_qc_executor(
        "feature_id,symbol,unique_name\nMT-X,MT-X,MT-X\nG1,G1,G1\n",
        QC_HEADER + b"c1,1,1,100,\nc2,4,2,25,\n",
    )
    result = run_sc_qc(str(source), executor, tmp_path / "data" / "analysis" / "sc_qc")
    assert result["status"] == "needs_confirmation", result
    return result


def _client(tmp_path, result: dict) -> TestClient:
    agent = FakeAgent()
    agent.workflow_manager = SimpleNamespace(
        workflow_recorder=SimpleNamespace(repo_root=tmp_path),
        run_analysis_for_agent=lambda *args: result,
    )
    return TestClient(create_app(agent))


def test_registers_qc_run_and_local_asset(tmp_path, source):
    result = _qc_result(tmp_path, source)
    sid = SessionStore().create_session()
    client = _client(tmp_path, result)

    response = client.post(f"/api/sessions/{sid}/local-input", json={"path": f'"{source}"'})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_confirmation"
    assert body["qc_run_id"] == result["results"]["qc_run_id"]
    store = SessionStore()
    assert store.get_messages(sid)[-1]["results"]["qc_run_id"] == body["qc_run_id"]
    asset = store.get_session(sid)["downloaded_assets"][0]
    assert asset == {
        "source": "local",
        "asset_id": "counts.csv",
        "access_path": str(source.resolve()),
    }
    assert client.get(f"/api/sessions/{sid}/qc/{body['qc_run_id']}").status_code == 200


def test_invalid_input_returns_400_without_message(tmp_path, source):
    result = {
        "status": "needs_input",
        "type": "analysis",
        "analysis_type": "single_cell",
        "message": "未检测到数据文件",
        "results": {},
    }
    sid = SessionStore().create_session()
    client = _client(tmp_path, result)

    response = client.post(f"/api/sessions/{sid}/local-input", json={"path": str(source)})

    assert response.status_code == 400
    assert response.json()["detail"] == "未检测到数据文件"
    assert SessionStore().get_messages(sid) == []


def test_qc_failure_is_persisted_with_evidence(tmp_path, source):
    result = {
        "status": "error",
        "type": "analysis",
        "analysis_type": "single_cell",
        "stage": "qc_review",
        "message": "QC 未完成；本地证据已保留，未执行下游分析。",
        "results": {"qc_artifacts": {"stderr.txt": "R failed"}},
    }
    sid = SessionStore().create_session()
    client = _client(tmp_path, result)

    response = client.post(f"/api/sessions/{sid}/local-input", json={"path": str(source)})

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    stored = SessionStore().get_messages(sid)[-1]
    assert stored["results"] == {"qc_artifacts": {"stderr.txt": "R failed"}}


def test_unknown_session_and_missing_path_are_rejected(tmp_path, source):
    client = _client(tmp_path, {})
    assert (
        client.post("/api/sessions/nope/local-input", json={"path": str(source)}).status_code == 404
    )

    sid = SessionStore().create_session()
    missing = source.parent / "missing"
    response = client.post(f"/api/sessions/{sid}/local-input", json={"path": str(missing)})
    assert response.status_code == 400
    assert "路径不存在" in response.json()["detail"]


def test_busy_session_returns_409(tmp_path, source):
    sid = SessionStore().create_session()
    client = _client(tmp_path, {})
    lock = session_lock(sid)
    assert lock.acquire(blocking=False)
    try:
        response = client.post(f"/api/sessions/{sid}/local-input", json={"path": str(source)})
    finally:
        lock.release()
    assert response.status_code == 409


def test_local_input_runs_tenx_directory_without_llm(tmp_path, monkeypatch):
    """真实 validate_tenx + run_sc_qc；R 用证据桩替代，验证 10x 目录路径。"""
    monkeypatch.chdir(tmp_path)
    tenx = tmp_path / "hg19"
    tenx.mkdir()
    (tenx / "matrix.mtx").write_text(
        "%%MatrixMarket matrix coordinate integer general\n2 2 3\n1 1 1\n1 2 1\n2 2 3\n",
        encoding="utf-8",
    )
    (tenx / "barcodes.tsv").write_text("c1\nc2\n", encoding="utf-8")
    (tenx / "features.tsv").write_text(
        "g1\tMT-X\tGene Expression\ng2\tG1\tGene Expression\n", encoding="utf-8"
    )
    executor = fake_qc_executor(
        "feature_id,symbol,unique_name\ng1,MT-X,g1\ng2,G1,g2\n",
        QC_HEADER + b"c1,1,1,100,\nc2,4,2,25,\n",
    )
    agent = FakeAgent()
    agent.workflow_manager = WorkflowManager(
        intent_parser=None,
        knowledge_client=None,
        r_executor=executor,
        visualizer=None,
    )
    sid = SessionStore().create_session()
    client = TestClient(create_app(agent))

    response = client.post(f"/api/sessions/{sid}/local-input", json={"path": str(tenx)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_confirmation"
    assert body["message"]["results"]["qc"]["cells"] == 2
    assert body["message"]["results"]["qc"]["features"] == 2
    assert body["message"]["results"]["qc_run_id"] == body["qc_run_id"]
