"""项目 CRUD 与会话归属端点。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import routes_projects
from src.api.app import create_app
from tests.unit.fakes import FakeAgent


@pytest.fixture
def client():
    return TestClient(create_app(FakeAgent()))


def test_project_crud(client, tmp_path):
    workdir = tmp_path / "liver"
    workdir.mkdir()
    created = client.post("/api/projects", json={"label": "肝癌项目", "root_path": str(workdir)})
    assert created.status_code == 200
    project = created.json()["project"]
    assert project["label"] == "肝癌项目"
    assert project["session_count"] == 0

    renamed = client.patch(f"/api/projects/{project['id']}", json={"label": "肝癌 2026"})
    assert renamed.json()["project"]["label"] == "肝癌 2026"

    cleared = client.patch(f"/api/projects/{project['id']}", json={"root_path": None})
    assert cleared.json()["project"]["root_path"] is None

    archived = client.patch(f"/api/projects/{project['id']}", json={"archived": True})
    assert archived.json()["project"]["archived_at"] is not None
    assert client.get("/api/projects").json()["projects"] == []
    assert (
        client.get("/api/projects?include_archived=1").json()["projects"][0]["id"] == project["id"]
    )

    client.patch(f"/api/projects/{project['id']}", json={"archived": False})
    assert client.delete(f"/api/projects/{project['id']}").status_code == 405


def test_create_project_rejects_blank_label(client):
    assert client.post("/api/projects", json={"label": "   "}).status_code == 422


def test_create_project_derives_label_from_root_path(client, tmp_path):
    workdir = tmp_path / "liver"
    workdir.mkdir()

    response = client.post("/api/projects", json={"root_path": str(workdir)})

    assert response.status_code == 200
    assert response.json()["project"]["label"] == "liver"
    assert client.post("/api/projects", json={}).status_code == 422


def test_project_root_path_must_exist_and_be_dir(client, tmp_path):
    assert (
        client.post("/api/projects", json={"label": "相对", "root_path": "data/liver"}).status_code
        == 422
    )

    missing = tmp_path / "missing"
    assert (
        client.post("/api/projects", json={"label": "缺失", "root_path": str(missing)}).status_code
        == 422
    )

    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    assert (
        client.post(
            "/api/projects", json={"label": "文件", "root_path": str(file_path)}
        ).status_code
        == 422
    )

    pid = client.post("/api/projects", json={"label": "项目"}).json()["project"]["id"]
    assert client.patch(f"/api/projects/{pid}", json={"root_path": str(missing)}).status_code == 422


def test_project_root_path_must_be_writable(client, tmp_path, monkeypatch):
    workdir = tmp_path / "locked"
    workdir.mkdir()
    monkeypatch.setattr(routes_projects, "writable_dir", lambda _path: False)

    response = client.post("/api/projects", json={"label": "不可写", "root_path": str(workdir)})
    assert response.status_code == 422
    assert "不可写" in response.json()["detail"]


def test_project_root_path_rejects_internal_data_dirs(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    internal = tmp_path / "data" / "workspace"
    internal.mkdir(parents=True)

    response = client.post("/api/projects", json={"label": "内部目录", "root_path": str(internal)})
    assert response.status_code == 422
    assert "应用数据目录" in response.json()["detail"]


def test_project_usage_returns_readonly_summary(client):
    pid = client.post("/api/projects", json={"label": "项目"}).json()["project"]["id"]

    usage = client.get(f"/api/projects/{pid}/usage").json()
    assert usage["scope"] == "project"
    assert usage["bytes"] == 0
    assert client.get("/api/projects/nope/usage").status_code == 404


def test_delete_project_is_cli_only(client):
    pid = client.post("/api/projects", json={"label": "非空"}).json()["project"]["id"]
    client.post("/api/sessions", json={"project_id": pid})

    response = client.delete(f"/api/projects/{pid}")
    assert response.status_code == 405
