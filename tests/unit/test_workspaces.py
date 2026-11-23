"""访客 cookie、临时工作区隔离与越权访问。"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from src.api import auth
from src.api.app import create_app
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent


def _client(base_url: str = "http://testserver") -> TestClient:
    return TestClient(create_app(FakeAgent()), base_url=base_url)


def test_first_visit_creates_httponly_visitor_cookie():
    client = _client()
    response = client.get("/api/workspace")

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["kind"] == "ephemeral"
    assert workspace["remaining_seconds"] > 23 * 3600
    set_cookie = response.headers["set-cookie"].lower()
    assert auth.VISITOR_COOKIE in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_https_cookie_is_secure():
    client = _client("https://testserver")
    response = client.get("/api/workspace")

    assert "secure" in response.headers["set-cookie"].lower()


def test_refresh_reuses_workspace_and_data():
    client = _client()
    first = client.get("/api/workspace").json()["workspace"]["id"]
    sid = client.post("/api/sessions", json={"title": "访客会话"}).json()["session"]["id"]

    assert client.get("/api/workspace").json()["workspace"]["id"] == first
    assert [row["id"] for row in client.get("/api/sessions").json()["sessions"]] == [sid]


def test_two_browsers_are_isolated():
    first = _client()
    second = _client()
    sid = first.post("/api/sessions", json={"title": "只属于 A"}).json()["session"]["id"]
    project_id = first.post("/api/projects", json={"label": "A 项目"}).json()["project"]["id"]
    wid = first.get("/api/workspace").json()["workspace"]["id"]
    SessionStore(workspace_id=wid).register_qc_run(sid, "run-a", "/tmp/qc", {"status": "ok"})

    assert (
        first.get("/api/workspace").json()["workspace"]["id"]
        != (second.get("/api/workspace").json()["workspace"]["id"])
    )
    assert second.get("/api/sessions").json()["sessions"] == []
    assert second.get("/api/projects").json()["projects"] == []
    assert second.get(f"/api/sessions/{sid}/messages").status_code == 404
    assert second.get(f"/api/sessions/{sid}/qc/run-a").status_code == 404
    assert second.patch(f"/api/projects/{project_id}", json={"label": "偷改"}).status_code == 404


def test_new_workspace_sets_one_cookie_and_uses_it():
    client = _client()
    response = client.post("/api/workspace/new")

    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert len([cookie for cookie in cookies if auth.VISITOR_COOKIE in cookie]) == 1
    created = response.json()["workspace"]["id"]
    assert client.get("/api/workspace").json()["workspace"]["id"] == created


def test_expired_visitor_gets_410_then_can_start_new_workspace():
    client = _client()
    old_workspace = client.get("/api/workspace").json()["workspace"]
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    almost = auth._utcnow() + timedelta(hours=23)
    future = auth._utcnow() + timedelta(hours=25)
    original = auth._utcnow

    auth._utcnow = lambda: almost
    try:
        assert client.get("/api/workspace").json()["workspace"]["id"] == old_workspace["id"]
    finally:
        auth._utcnow = original

    auth._utcnow = lambda: future
    try:
        expired = client.get("/api/workspace")
        assert expired.status_code == 410
        assert expired.json()["code"] == "workspace_expired"
        new_workspace = client.post("/api/workspace/new").json()["workspace"]
        assert new_workspace["id"] != old_workspace["id"]
        assert client.get(f"/api/sessions/{sid}/messages").status_code == 404
    finally:
        auth._utcnow = original


def test_ephemeral_workspace_rejects_writes_at_two_gigabytes(monkeypatch):
    client = _client()
    client.get("/api/workspace")
    monkeypatch.setattr(auth, "workspace_usage_bytes", lambda *_: auth.MAX_WORKSPACE_BYTES)

    response = client.post("/api/sessions", json={})

    assert response.status_code == 413
    assert response.json()["code"] == "workspace_quota"


def test_visitor_cannot_use_server_paths(tmp_path):
    client = _client()
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]

    assert (
        client.post(f"/api/sessions/{sid}/local-input", json={"path": str(tmp_path)}).status_code
        == 403
    )
    assert client.post("/api/projects", json={"root_path": str(tmp_path)}).status_code == 403
