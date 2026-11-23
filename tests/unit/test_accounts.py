"""管理账户登录、退出、隔离与限速。"""

from __future__ import annotations

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from src.api import auth
from src.api.app import create_app
from tests.unit.fakes import FakeAgent


def _client(base_url: str = "http://testserver") -> TestClient:
    return TestClient(create_app(FakeAgent()), base_url=base_url)


def _add_account(client: TestClient, username: str, password: str = "correct-password"):
    store = client.app.state.store_factory()
    workspace_id = store.create_workspace(kind="persistent", label=username)
    account_id = store.create_account(
        username,
        PasswordHasher().hash(password),
        username,
        workspace_id,
    )
    return store, workspace_id, account_id


def test_login_sets_account_cookie_and_persistent_workspace():
    client = _client()
    _, workspace_id, _ = _add_account(client, "admin1")

    response = client.post(
        "/api/auth/login", json={"username": "admin1", "password": "correct-password"}
    )

    assert response.status_code == 200
    assert response.json()["workspace"]["kind"] == "persistent"
    set_cookie = response.headers["set-cookie"].lower()
    assert auth.ACCOUNT_COOKIE in set_cookie
    assert "httponly" in set_cookie
    current = client.get("/api/workspace").json()["workspace"]
    assert current["id"] == workspace_id
    assert current["principal_kind"] == "account"


def test_https_account_cookie_is_secure():
    client = _client("https://testserver")
    _add_account(client, "admin1")
    response = client.post(
        "/api/auth/login", json={"username": "admin1", "password": "correct-password"}
    )
    assert "secure" in response.headers["set-cookie"].lower()


def test_login_failure_does_not_distinguish_unknown_user():
    client = _client()
    _add_account(client, "admin1")
    wrong = client.post("/api/auth/login", json={"username": "admin1", "password": "wrong"})
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": "wrong"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_accounts_are_isolated():
    first = _client()
    second = _client()
    _add_account(first, "admin1")
    _add_account(second, "admin2")
    first.post("/api/auth/login", json={"username": "admin1", "password": "correct-password"})
    second.post("/api/auth/login", json={"username": "admin2", "password": "correct-password"})

    sid = first.post("/api/sessions", json={"title": "admin1 会话"}).json()["session"]["id"]
    assert second.get("/api/sessions").json()["sessions"] == []
    assert second.get(f"/api/sessions/{sid}/messages").status_code == 404


def test_account_rename_changes_login_name_without_moving_workspace():
    client = _client()
    store, workspace_id, account_id = _add_account(client, "admin1")
    store.set_account_username(account_id, "admin_yao")
    store.set_account_display_name(account_id, "admin_yao")

    old = client.post(
        "/api/auth/login", json={"username": "admin1", "password": "correct-password"}
    )
    new = client.post(
        "/api/auth/login", json={"username": "admin_yao", "password": "correct-password"}
    )

    assert old.status_code == 401
    assert new.status_code == 200
    assert new.json()["workspace"]["id"] == workspace_id


def test_logout_revokes_token_and_returns_to_visitor_workspace():
    client = _client()
    visitor_workspace = client.get("/api/workspace").json()["workspace"]["id"]
    _add_account(client, "admin1")
    client.post("/api/auth/login", json={"username": "admin1", "password": "correct-password"})

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert client.get("/api/workspace").json()["workspace"]["id"] == visitor_workspace
    store = client.app.state.store_factory()
    assert store.list_accounts()[0]["username"] == "admin1"


def test_disabled_account_cannot_login():
    client = _client()
    store, _, account_id = _add_account(client, "admin1")
    store.set_account_disabled(account_id, True)

    response = client.post(
        "/api/auth/login", json={"username": "admin1", "password": "correct-password"}
    )
    assert response.status_code == 401


def test_login_rate_limit():
    client = _client()
    _add_account(client, "admin1")
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin1", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"username": "admin1", "password": "correct-password"}
        ).status_code
        == 429
    )
