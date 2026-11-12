"""项目 CRUD 与会话归属端点。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from tests.unit.fakes import FakeAgent


@pytest.fixture
def client():
    return TestClient(create_app(FakeAgent()))


def test_project_crud(client):
    created = client.post("/api/projects", json={"label": "肝癌项目", "root_path": "D:/data/liver"})
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
