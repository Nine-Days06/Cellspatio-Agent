"""会话 CRUD 与消息恢复端点。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent


@pytest.fixture
def client():
    return TestClient(create_app(FakeAgent()))


def _pending_message(store: SessionStore, session_id: str, status: str = "pending") -> None:
    store.append_message(
        session_id, "user", "做差异表达",
    )
    store.append_message(
        session_id, "assistant", "脚本如下",
        pending_script={
            "script": "library(DESeq2)",
            "analysis_type": "differential_expression",
            "params": {"group_by": "condition"},
            "method_context": None,
            "user_request": "做差异表达",
            "status": status,
        },
    )


def test_list_sessions_empty(client):
    response = client.get("/api/sessions")

    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_create_session_returns_new_session(client):
    response = client.post("/api/sessions", json={"title": "肝癌项目"})

    assert response.status_code == 200
    body = response.json()["session"]
    assert body["title"] == "肝癌项目"
    assert body["downloaded_assets"] == []
    assert [s["id"] for s in client.get("/api/sessions").json()["sessions"]] == [body["id"]]


def test_create_session_without_body_uses_default_title(client):
    response = client.post("/api/sessions")

    assert response.status_code == 200
    assert response.json()["session"]["title"] == "新会话"


def test_delete_session_removes_it(client):
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]

    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/sessions/{sid}/messages").status_code == 404
    assert client.get("/api/sessions").json()["sessions"] == []


def test_delete_unknown_session_returns_404(client):
    assert client.delete("/api/sessions/nope").status_code == 404


def test_messages_of_unknown_session_returns_404(client):
    assert client.get("/api/sessions/nope/messages").status_code == 404


def test_messages_response_shape_and_first_user_message_sets_title(client):
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    SessionStore().append_message(sid, "user", "分析这份数据")

    body = client.get(f"/api/sessions/{sid}/messages").json()

    assert set(body) == {"session", "messages", "pending_script", "soft_warn"}
    assert body["session"]["title"] == "分析这份数据"
    assert body["messages"][0]["content"] == "分析这份数据"
    assert body["pending_script"] is None
    assert body["soft_warn"] is False


def test_messages_are_json_serialisable_with_results(client):
    import plotly.graph_objects as go

    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    SessionStore().append_message(
        sid, "assistant", "分析完成", results={"charts": [go.Figure(go.Scatter(x=[1], y=[2]))]}
    )

    body = client.get(f"/api/sessions/{sid}/messages")

    assert body.status_code == 200
    charts = body.json()["messages"][0]["results"]["charts"]
    assert charts[0]["type"] == "plotly"
    json.dumps(body.json())


def test_restore_expires_pending_script(client):
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    _pending_message(SessionStore(), sid)

    body = client.get(f"/api/sessions/{sid}/messages?restore=1").json()

    assert body["pending_script"]["status"] == "expired"
    assert body["pending_script"]["user_request"] == "做差异表达"
    # 过期状态已落库
    latest = [m for m in SessionStore().get_messages(sid) if m["pending_script"]][-1]
    assert latest["pending_script"]["status"] == "expired"


def test_restore_keeps_already_expired_script(client):
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    _pending_message(SessionStore(), sid, status="expired")

    body = client.get(f"/api/sessions/{sid}/messages?restore=1").json()

    assert body["pending_script"]["status"] == "expired"


def test_restore_ignores_confirmed_script(client):
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    _pending_message(SessionStore(), sid, status="confirmed")

    body = client.get(f"/api/sessions/{sid}/messages?restore=1").json()

    assert body["pending_script"] is None


def test_without_restore_pending_script_stays_pending(client):
    """done 后前端对账重拉不能误把 pending 判过期。"""
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    _pending_message(SessionStore(), sid)

    body = client.get(f"/api/sessions/{sid}/messages").json()

    assert body["pending_script"] is None
    latest = [m for m in SessionStore().get_messages(sid) if m["pending_script"]][-1]
    assert latest["pending_script"]["status"] == "pending"


def test_only_latest_pending_script_is_considered(client):
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    store = SessionStore()
    _pending_message(store, sid, status="confirmed")
    _pending_message(store, sid, status="pending")

    body = client.get(f"/api/sessions/{sid}/messages?restore=1").json()

    assert body["pending_script"]["status"] == "expired"
    assert body["pending_script"]["user_request"] == "做差异表达"


def test_soft_warn_flag_reflects_context_size(client, monkeypatch):
    from src.control import compact

    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    SessionStore().append_message(sid, "user", "短")
    monkeypatch.setattr(compact, "should_soft_warn", lambda summary, window: True)

    body = client.get(f"/api/sessions/{sid}/messages").json()

    assert body["soft_warn"] is True


def test_create_app_mounts_web_dist_only_when_present(monkeypatch, tmp_path):
    """web/dist 缺失时不挂静态目录，存在时才挂 —— 用 tmp_path 驱动，不依赖仓库真实状态。"""
    from src.api import app as api_app

    monkeypatch.setattr(api_app, "_WEB_DIST", tmp_path / "absent")
    app = create_app(FakeAgent())

    assert app.state.agent is not None
    # include_router 后路由在 _IncludedRouter.effective_candidates() 中
    def _collect_paths(app_):
        paths = set()
        for r in app_.routes:
            if hasattr(r, "effective_candidates"):
                for c in r.effective_candidates():
                    paths.add(c.path_format)
            else:
                paths.add(getattr(r, "path_format", getattr(r, "path", "")))
        return paths

    route_paths = _collect_paths(app)
    assert "/api/sessions" in route_paths
    assert "/api/sessions/{session_id}/messages" in route_paths
    # 无 web/dist 时不应有 StaticFiles 挂载（Mount 路径为 /{path}）
    assert "/{path}" not in route_paths

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(api_app, "_WEB_DIST", dist)
    mounted = create_app(FakeAgent())

    mounted_paths = _collect_paths(mounted)
    assert "/{path}" in mounted_paths