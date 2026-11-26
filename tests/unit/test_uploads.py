"""访客上传：限额、压缩包安全、工作区落盘与 QC 资产。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.analysis.sc_qc import run_sc_qc
from src.api import routes_upload
from src.api.app import create_app
from tests.unit.fakes import QC_HEADER, FakeAgent, fake_qc_executor


def _qc_result(output_root: Path, source: Path) -> dict:
    executor = fake_qc_executor(
        "feature_id,symbol,unique_name\nMT-X,MT-X,MT-X\nG1,G1,G1\n",
        QC_HEADER + b"c1,1,1,100,\nc2,4,2,25,\n",
    )
    result = run_sc_qc(str(source), executor, output_root)
    assert result["status"] == "needs_confirmation", result
    return result


def _client(tmp_path, result: dict) -> TestClient:
    agent = FakeAgent()
    agent.workflow_manager = SimpleNamespace(
        workflow_recorder=SimpleNamespace(repo_root=tmp_path),
        run_analysis_for_agent=lambda *args: result,
    )
    return TestClient(create_app(agent))


def _zip_bytes(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "target")
    return buffer.getvalue()


def test_upload_csv_runs_qc_and_stays_in_workspace(tmp_path):
    source = tmp_path / "counts.csv"
    source.write_text("gene,c1,c2\nMT-X,1,1\nG1,0,3\n", encoding="utf-8")
    client = _client(tmp_path, {})
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    workspace = next(
        item
        for item in client.app.state.store_factory().list_workspaces()
        if item["kind"] == "ephemeral"
    )
    result = _qc_result(Path(workspace["root_path"]) / "analysis" / "sc_qc", source)
    client.app.state.agent.workflow_manager.run_analysis_for_agent = lambda *args: result

    response = client.post(
        f"/api/sessions/{sid}/upload",
        files={"file": ("counts.csv", source.read_bytes(), "text/csv")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_confirmation", response.text
    asset = client.app.state.store_factory().get_session(sid)["downloaded_assets"][0]
    assert asset["source"] == "upload"
    assert "uploads" in asset["access_path"]
    assert str(tmp_path) in asset["access_path"]


def test_upload_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(_zip_bytes({"../escape.txt": b"x"}))
    with pytest.raises(routes_upload.UploadRejected, match="不安全"):
        routes_upload.extract_zip(archive, tmp_path / "out")


def test_upload_rejects_symlink(tmp_path):
    archive = tmp_path / "symlink.zip"
    archive.write_bytes(_zip_bytes({"matrix.mtx": b"x"}, symlink="barcodes.tsv"))
    with pytest.raises(routes_upload.UploadRejected):
        routes_upload.extract_zip(archive, tmp_path / "out")


def test_upload_accepts_tenx_triplet_and_rejects_oversized_extraction(tmp_path, monkeypatch):
    archive = tmp_path / "tenx.zip"
    archive.write_bytes(
        _zip_bytes(
            {
                "sample/matrix.mtx": b"%%MatrixMarket\n",
                "sample/barcodes.tsv": b"c1\n",
                "sample/features.tsv": b"G1\n",
            }
        )
    )
    assert routes_upload.extract_zip(archive, tmp_path / "out").name == "sample"

    monkeypatch.setattr(routes_upload, "MAX_REQUEST_BYTES", 1)
    with pytest.raises(routes_upload.UploadRejected, match="300 MB"):
        routes_upload.extract_zip(archive, tmp_path / "out2")


def test_upload_rejects_oversized_file_and_cleans_up(tmp_path, monkeypatch):
    source = tmp_path / "counts.csv"
    source.write_text("gene,c1\nG1,1\n", encoding="utf-8")
    client = _client(tmp_path, {})
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    monkeypatch.setattr(routes_upload, "MAX_FILE_BYTES", 3)

    response = client.post(
        f"/api/sessions/{sid}/upload",
        files={"file": ("counts.csv", source.read_bytes(), "text/csv")},
    )

    assert response.status_code == 413
    workspace_id = next(
        item["id"]
        for item in client.app.state.store_factory().list_workspaces()
        if item["kind"] == "ephemeral"
    )
    uploads = tmp_path / "data" / "workspaces" / workspace_id / "uploads"
    assert not [path for path in uploads.rglob("*") if path.is_file()]


def test_upload_rejects_cumulative_limit(tmp_path, monkeypatch):
    source = tmp_path / "counts.csv"
    source.write_text("gene,c1\nG1,1\n", encoding="utf-8")
    client = _client(tmp_path, {})
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    monkeypatch.setattr(routes_upload, "MAX_UPLOAD_TOTAL_BYTES", 3)

    response = client.post(
        f"/api/sessions/{sid}/upload",
        files={"file": ("counts.csv", source.read_bytes(), "text/csv")},
    )

    assert response.status_code == 413
    assert not [path for path in (tmp_path / "data" / "workspaces").rglob("*") if path.is_file()]
