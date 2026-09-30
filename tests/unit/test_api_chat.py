"""POST /api/chat：SSE 事件序列、404 / 409、error 事件、aborted 落库。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

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


def test_concurrent_generation_on_same_session_returns_409(client):
    sid = _new_session(client)
    lock = session_lock(sid)
    assert lock.acquire(blocking=False) is True
    try:
        response = client.post("/api/chat", json={"session_id": sid, "prompt": "hi"})
    finally:
        lock.release()

    assert response.status_code == 409
    assert "生成任务" in response.json()["detail"]


def test_lock_is_released_after_stream_completes(client):
    sid = _new_session(client)

    _stream(client, sid, "hi")

    assert session_lock(sid).acquire(blocking=False) is True
    session_lock(sid).release()


def test_runtime_exception_becomes_error_event(client):
    sid = _new_session(client)

    class BoomRuntime:
        def execute(self, user_input, context=None, on_event=None):
            raise RuntimeError("知识库炸了")

    client.app.state.agent.agent_runtime = BoomRuntime()

    events = _stream(client, sid, "问个问题")

    assert events[-1] == {"type": "error", "message": "知识库炸了", "retryable": True}


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
