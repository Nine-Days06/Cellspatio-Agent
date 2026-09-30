"""POST /api/chat：SSE 事件序列、404 / 409、error 事件；协作式中止链路由 T1/T3 分层覆盖。"""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from src.api import chat_service
from src.api.app import create_app
from src.api.chat_service import session_lock
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent, FakeRuntime


@pytest.fixture
def client():
    return TestClient(create_app(FakeAgent()))


def _new_session(client) -> str:
    return client.post("/api/sessions", json={}).json()["session"]["id"]


def _stream(client, session_id: str, prompt: str):
    response = client.post("/api/chat", json={"session_id": session_id, "prompt": prompt})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.endswith("\n\n")
    return [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_emits_user_message_and_done_sequence(client):
    sid = _new_session(client)
    app = client.app
    app.state.agent.agent_runtime = FakeRuntime(
        events=[{"type": "delta", "text": "TP53 "}, {"type": "delta", "text": "是抑癌基因"}],
        default={"status": "success", "type": "knowledge_response",
                 "response": "TP53 是抑癌基因", "references": []},
    )

    events = _stream(client, sid, "TP53 的作用？")

    assert [e["type"] for e in events] == ["delta", "delta", "done"]
    assert "".join(e["text"] for e in events[:2]) == "TP53 是抑癌基因"
    stored = SessionStore().get_messages(sid)
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert events[-1]["message_id"] == stored[-1]["id"]


def test_stream_emits_tool_status_with_label(client):
    sid = _new_session(client)
    client.app.state.agent.agent_runtime = FakeRuntime(
        events=[{"type": "tool_status", "name": "run_analysis", "phase": "start"}],
        default={"status": "success", "message": "分析完成", "results": None},
    )

    events = _stream(client, sid, "做差异表达")

    assert events[0] == {"type": "tool_status", "name": "run_analysis",
                         "phase": "start", "label": "运行分析"}


def test_stream_emits_confirm_card_for_script_branch(client):
    sid = _new_session(client)
    client.app.state.agent.agent_runtime = FakeRuntime(default={
        "status": "needs_script_confirmation",
        "message": "已生成 R 脚本",
        "script": "library(DESeq2)",
        "analysis_type": "differential_expression",
        "params": {},
    })

    events = _stream(client, sid, "做差异表达")

    assert [e["type"] for e in events] == ["confirm_card", "done"]
    assert events[0]["kind"] == "script"
    assert events[0]["payload"]["script"] == "library(DESeq2)"


def test_unknown_session_returns_404(client):
    assert client.post("/api/chat", json={"session_id": "nope", "prompt": "hi"}).status_code == 404
    # 404 在抢锁之前返回，不应创建锁条目
    assert "nope" not in chat_service._locks


def test_concurrent_generation_on_same_session_returns_409(client):
    sid = _new_session(client)
    lock = session_lock(sid)
    assert lock.acquire(blocking=False) is True
    try:
        response = client.post("/api/chat", json={"session_id": sid, "prompt": "hi"})
    finally:
        lock.release()

    assert response.status_code == 409
    # 文案与 confirm / delete 端点统一（不特指生成任务）
    assert response.json()["detail"] == "该会话已有任务在进行"


def test_lock_is_released_after_stream_completes(client):
    sid = _new_session(client)

    _stream(client, sid, "hi")

    assert session_lock(sid).acquire(blocking=False) is True
    session_lock(sid).release()


def test_thread_start_failure_releases_lock(monkeypatch):
    """M5：acquire 成功到 Thread.start 之间抛异常，锁不得泄漏成永久 409。"""
    client = TestClient(create_app(FakeAgent()), raise_server_exceptions=False)
    sid = _new_session(client)

    real_start = threading.Thread.start

    def _boom(self):
        # TestClient 自身的 portal 线程必须正常启动，只让 worker 起不来
        if self.name == "chat-worker":
            raise RuntimeError("thread start failed")
        return real_start(self)

    monkeypatch.setattr(threading.Thread, "start", _boom)

    response = client.post("/api/chat", json={"session_id": sid, "prompt": "hi"})

    assert response.status_code == 500
    # 锁已归还：会话仍可再次抢锁（未泄漏）
    assert session_lock(sid).acquire(blocking=False) is True
    session_lock(sid).release()


def test_stream_combines_delta_tool_status_done_sequence(client):
    """规格 §7：单条流内 delta → tool_status → done 组合序列。"""
    sid = _new_session(client)
    client.app.state.agent.agent_runtime = FakeRuntime(
        events=[{"type": "delta", "text": "开始分析。"},
                {"type": "tool_status", "name": "run_analysis", "phase": "start"}],
        default={"status": "success", "message": "分析完成", "results": None},
    )

    events = _stream(client, sid, "做差异表达")

    assert [e["type"] for e in events] == ["delta", "tool_status", "done"]
    assert events[1]["label"] == "运行分析"


def test_runtime_exception_becomes_error_event(client):
    sid = _new_session(client)

    class BoomRuntime:
        def execute(self, user_input, context=None, on_event=None):
            raise RuntimeError("知识库炸了")

    client.app.state.agent.agent_runtime = BoomRuntime()

    events = _stream(client, sid, "问个问题")

    assert events[-1] == {"type": "error", "message": "知识库炸了", "retryable": True}
    # error 事件产生后锁必须已释放，否则后续请求全部 409
    assert session_lock(sid).acquire(blocking=False) is True
    session_lock(sid).release()


def test_user_message_persisted_even_when_runtime_fails(client):
    sid = _new_session(client)

    class BoomRuntime:
        def execute(self, user_input, context=None, on_event=None):
            raise RuntimeError("boom")

    client.app.state.agent.agent_runtime = BoomRuntime()
    _stream(client, sid, "问个问题")

    stored = SessionStore().get_messages(sid)
    assert stored[0]["role"] == "user"
    assert stored[0]["content"] == "问个问题"
    assert len(stored) == 1
