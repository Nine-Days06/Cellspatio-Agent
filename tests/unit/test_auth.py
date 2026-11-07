"""多用户认证与会话隔离：开启鉴权后的登录、拦截、归属校验。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import auth
from src.api.app import create_app
from src.ui.session_store import SessionStore


class FakeAgent:
    """create_app 只读取 state 上的 agent；会话路由不触碰它。"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    monkeypatch.delenv("CELLSPATIO_AUTH_DISABLED", raising=False)
    auth._attempts.clear()
    return SessionStore()


@pytest.fixture
def client(store):
    return TestClient(create_app(FakeAgent()))


def _create_user(store: SessionStore, username: str) -> None:
    store.create_user(username, auth.hash_password("password123"))


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": "password123"}
    )
    assert response.status_code == 200, response.text


def test_password_hash_roundtrip():
    hashed = auth.hash_password("secret123")
    assert auth.verify_password(hashed, "secret123")
    assert not auth.verify_password(hashed, "wrong")


def test_api_requires_login(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_login_and_me(client, store):
    _create_user(store, "alice")
    _login(client, "alice")
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    assert "password_hash" not in me.json()["user"]


def test_wrong_password_rejected(client, store):
    _create_user(store, "bob")
    response = client.post("/api/auth/login", json={"username": "bob", "password": "nope"})
    assert response.status_code == 401


def test_sessions_are_isolated(client, store):
    _create_user(store, "alice")
    _create_user(store, "bob")

    _login(client, "alice")
    alice_session = client.post("/api/sessions", json={}).json()["session"]["id"]
    assert [s["id"] for s in client.get("/api/sessions").json()["sessions"]] == [alice_session]

    client.post("/api/auth/logout")
    _login(client, "bob")
    assert client.get("/api/sessions").json()["sessions"] == []
    assert client.get(f"/api/sessions/{alice_session}/messages").status_code == 404


def test_login_rate_limited(client, store):
    _create_user(store, "carol")
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "carol", "password": "nope"})
    response = client.post("/api/auth/login", json={"username": "carol", "password": "password123"})
    assert response.status_code == 429
