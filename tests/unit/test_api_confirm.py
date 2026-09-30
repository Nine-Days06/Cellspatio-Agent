"""POST /api/confirm：脚本确认 / 取消 / 数据下载确认三条流转。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.chat_service import session_lock
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent


@pytest.fixture
def client():
    return TestClient(create_app(FakeAgent()))


def _new_session(client) -> str:
    return client.post("/api/sessions", json={}).json()["session"]["id"]


def _with_pending_script(client, status: str = "pending") -> str:
    sid = _new_session(client)
    SessionStore().append_message(
        sid, "user", "做差异表达",
    )
    SessionStore().append_message(
        sid, "assistant", "```r\nlibrary(DESeq2)\n```",
        pending_script={
            "script": "library(DESeq2)",
            "analysis_type": "differential_expression",
            "params": {"group_by": "condition"},
            "method_context": "DESeq2 常规流程",
            "user_request": "做差异表达",
            "status": status,
        },
    )
    return sid


def _latest_pending(session_id: str):
    return [m for m in SessionStore().get_messages(session_id) if m["pending_script"]][-1]


def test_script_confirm_runs_script_and_persists_result(client):
    sid = _with_pending_script(client)
    client.app.state.agent.confirm_result = {
        "status": "success", "message": "分析完成", "results": None,
    }

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "script_confirm",
    })

    assert response.status_code == 200
    assert client.app.state.agent.confirm_calls == [{
        "analysis_type": "differential_expression",
        "params": {"group_by": "condition"},
        "script": "library(DESeq2)",
        "method_context": "DESeq2 常规流程",
    }]
    assert _latest_pending(sid)["pending_script"]["status"] == "confirmed"
    stored = SessionStore().get_messages(sid)
    assert stored[-1]["role"] == "assistant"
    assert "分析完成" in stored[-1]["content"]
    assert response.json()["message"]["id"] == stored[-1]["id"]


def test_script_confirm_without_pending_returns_409(client):
    sid = _new_session(client)

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "script_confirm",
    })

    assert response.status_code == 409
    assert "待确认" in response.json()["detail"]


def test_script_confirm_on_expired_returns_409(client):
    sid = _with_pending_script(client, status="expired")

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "script_confirm",
    })

    assert response.status_code == 409
    assert client.app.state.agent.confirm_calls == []


def test_script_cancel_marks_cancelled_and_persists_message(client):
    sid = _with_pending_script(client)

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "script_cancel",
    })

    assert response.status_code == 200
    assert _latest_pending(sid)["pending_script"]["status"] == "cancelled"
    stored = SessionStore().get_messages(sid)
    assert stored[-1]["content"] == "已取消本次脚本执行。"
    assert client.app.state.agent.confirm_calls == []


def test_data_confirm_appends_asset_to_session(client):
    sid = _new_session(client)
    client.app.state.agent.download_result = {
        "status": "success",
        "asset": {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"},
    }

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "data_confirm",
        "source": "geo", "asset_id": "GSE1", "query": "肝癌",
    })

    assert response.status_code == 200
    assert client.app.state.agent.download_calls == [
        {"source": "geo", "asset_id": "GSE1", "query": "肝癌"}
    ]
    session = SessionStore().get_session(sid)
    assert session["downloaded_assets"] == [
        {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"}
    ]
    stored = SessionStore().get_messages(sid)
    assert stored[-1]["content"] == "已下载 GSE1 → `data/raw/geo/GSE1/matrix.gz`"


def test_data_confirm_without_asset_persists_failure_message(client):
    sid = _new_session(client)
    client.app.state.agent.download_result = {"status": "error", "message": "下载失败"}

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "data_confirm",
        "source": "geo", "asset_id": "GSE1",
    })

    assert response.status_code == 200
    session = SessionStore().get_session(sid)
    assert session["downloaded_assets"] == []
    assert SessionStore().get_messages(sid)[-1]["content"] == "下载失败"


def test_data_confirm_missing_asset_id_returns_400(client):
    sid = _new_session(client)

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "data_confirm", "source": "geo",
    })

    assert response.status_code == 400


def test_unknown_action_returns_400(client):
    sid = _new_session(client)

    response = client.post("/api/confirm", json={"session_id": sid, "action": "script_regen"})

    assert response.status_code == 400
    assert "script_regen" in response.json()["detail"]


def test_confirm_unknown_session_returns_404(client):
    response = client.post("/api/confirm", json={
        "session_id": "nope", "action": "script_confirm",
    })

    assert response.status_code == 404


def test_confirm_while_generation_running_returns_409(client):
    sid = _new_session(client)
    lock = session_lock(sid)
    assert lock.acquire(blocking=False) is True
    try:
        response = client.post("/api/confirm", json={
            "session_id": sid, "action": "data_confirm",
            "source": "geo", "asset_id": "GSE1",
        })
    finally:
        lock.release()

    assert response.status_code == 409
    # confirm 端点在执行期间也可能是本端点自身持有锁 → 文案保持中性
    assert response.json()["detail"] == "该会话已有任务在进行"


def test_data_confirm_repeat_keeps_single_asset(client):
    """幂等：同一 (source, asset_id) 连续两次确认，资产只落库一次。"""
    sid = _new_session(client)
    client.app.state.agent.download_result = {
        "status": "success",
        "asset": {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"},
    }
    payload = {
        "session_id": sid, "action": "data_confirm",
        "source": "geo", "asset_id": "GSE1", "query": "肝癌",
    }

    first = client.post("/api/confirm", json=payload)
    second = client.post("/api/confirm", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200  # 重复确认仍返回成功消息
    session = SessionStore().get_session(sid)
    assert session["downloaded_assets"] == [
        {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"}
    ]
    assert "已下载 GSE1" in SessionStore().get_messages(sid)[-1]["content"]


def test_data_confirm_unregistered_source_returns_structured_400():
    """未注册 source（KeyError）→ 4xx JSON detail，而非 Starlette 纯文本 500。"""
    client = TestClient(create_app(FakeAgent()), raise_server_exceptions=False)
    sid = _new_session(client)

    def _boom(source, asset_id, query=""):
        raise KeyError(f"未注册的数据源: {source}")

    client.app.state.agent.confirm_and_download = _boom

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "data_confirm",
        "source": "not_registered", "asset_id": "X1",
    })

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert "not_registered" in response.json()["detail"]


def test_script_confirm_twice_second_gets_409(client):
    """脚本确认幂等锁：成功后再次 confirm → 409，执行器只被调用一次。"""
    sid = _with_pending_script(client)
    client.app.state.agent.confirm_result = {
        "status": "success", "message": "分析完成", "results": None,
    }
    payload = {"session_id": sid, "action": "script_confirm"}

    first = client.post("/api/confirm", json=payload)
    second = client.post("/api/confirm", json=payload)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "待确认" in second.json()["detail"]
    assert len(client.app.state.agent.confirm_calls) == 1


def test_script_confirm_execute_failure_returns_502_keeps_pending():
    """执行器抛异常 → 502 结构化 JSON，pending 状态不得被写成 confirmed。"""
    client = TestClient(create_app(FakeAgent()), raise_server_exceptions=False)
    sid = _with_pending_script(client)

    def _boom(*args, **kwargs):
        raise RuntimeError("R 执行崩溃")

    client.app.state.agent.execute_confirmed_script = _boom

    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "script_confirm",
    })

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert "R 执行崩溃" in response.json()["detail"]
    assert _latest_pending(sid)["pending_script"]["status"] == "pending"
