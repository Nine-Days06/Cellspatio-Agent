"""chat_service.run_turn：落库时机、分支流转、事件顺序、会话锁。"""
from __future__ import annotations

import json

import pytest

from src.api.chat_service import run_turn, session_lock
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent, FakeRuntime


@pytest.fixture
def store():
    return SessionStore()


def _sink(events=None, abort_types=()):
    collected = list(events or [])

    def emit(event):
        collected.append(event)
        return event.get("type") not in abort_types

    emit.collected = collected
    return emit


def test_user_message_is_persisted_before_runtime_runs(store):
    sid = store.create_session()
    roles_at_execute: list[list[str]] = []

    class RecordingRuntime(FakeRuntime):
        """执行瞬间回读 store，验证用户消息在运行时启动前已落库。"""

        def execute(self, user_input, context=None, on_event=None):
            roles_at_execute.append([m["role"] for m in store.get_messages(sid)])
            return super().execute(user_input, context, on_event)

    runtime = RecordingRuntime()
    emit = _sink()

    run_turn(FakeAgent(runtime=runtime), store, sid, "你好", emit)

    # 运行时执行时回读：仅 user 已在库（assistant 尚未写入）
    assert roles_at_execute == [["user"]]
    messages = store.get_messages(sid)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "你好"
    assert len(runtime.calls) == 1
    assert emit.collected[-1]["type"] == "done"


def test_general_branch_persists_assistant_message_and_done_event(store):
    sid = store.create_session()
    runtime = FakeRuntime(default={"status": "success", "type": "knowledge_response",
                                   "response": "抑癌基因", "references": []})
    emit = _sink()

    result = run_turn(FakeAgent(runtime=runtime), store, sid, "TP53?", emit)

    assert result["status"] == "success"
    last = store.get_messages(sid)[-1]
    assert last["role"] == "assistant"
    assert "抑癌基因" in last["content"]
    done = emit.collected[-1]
    assert done["type"] == "done"
    assert done["message_id"] == last["id"]
    json.dumps(done)  # 不得抛 TypeError


def test_chart_events_precede_done_event(store):
    import plotly.graph_objects as go

    sid = store.create_session()
    fig = go.Figure(go.Scatter(x=[1], y=[2]))
    runtime = FakeRuntime(default={"status": "success", "message": "ok",
                                   "results": {"charts": [fig]}})
    emit = _sink()

    run_turn(FakeAgent(runtime=runtime), store, sid, "分析", emit)

    types = [e["type"] for e in emit.collected]
    assert types[-1] == "done"
    assert "chart" in types
    assert store.get_messages(sid)[-1]["results"]["charts"][0].data is not None


def test_script_confirmation_branch_persists_pending_script(store):
    sid = store.create_session()
    runtime = FakeRuntime(default={
        "status": "needs_script_confirmation",
        "message": "已生成 R 脚本",
        "script": "library(DESeq2)",
        "analysis_type": "differential_expression",
        "params": {"group_by": "condition"},
        "method_context": "DESeq2 常规流程",
    })
    emit = _sink()

    result = run_turn(FakeAgent(runtime=runtime), store, sid, "做差异表达", emit)

    assert result["status"] == "needs_script_confirmation"
    last = store.get_messages(sid)[-1]
    assert last["pending_script"] == {
        "script": "library(DESeq2)",
        "analysis_type": "differential_expression",
        "params": {"group_by": "condition"},
        "method_context": "DESeq2 常规流程",
        "user_request": "做差异表达",
        "status": "pending",
    }
    assert "```r" in last["content"]
    card = emit.collected[0]
    assert card["type"] == "confirm_card"
    assert card["kind"] == "script"
    assert card["payload"]["message_id"] == last["id"]
    assert card["payload"]["user_request"] == "做差异表达"
    assert emit.collected[-1]["type"] == "done"


def test_data_confirmation_branch_keeps_candidates_out_of_database(store):
    sid = store.create_session()
    candidates = [{"source": "geo", "asset_id": "GSE1", "title": "T"}]
    runtime = FakeRuntime(default={
        "status": "needs_confirmation",
        "type": "fetch_data",
        "candidates": candidates,
        "query": "肝癌",
        "message": "找到 1 个候选数据集，请选择要下载的项：",
    })
    emit = _sink()

    result = run_turn(FakeAgent(runtime=runtime), store, sid, "下载 GSE1", emit)

    assert result["status"] == "needs_confirmation"
    last = store.get_messages(sid)[-1]
    assert last["content"] == "找到 1 个候选数据集，请选择要下载的项："
    assert last["results"] is None
    assert last["pending_script"] is None
    card = emit.collected[0]
    assert card["kind"] == "data"
    assert card["payload"]["candidates"] == candidates
    assert card["payload"]["query"] == "肝癌"
    assert card["payload"]["message"] == "找到 1 个候选数据集，请选择要下载的项："


def test_aborted_branch_persists_interrupted_marker(store):
    sid = store.create_session()
    runtime = FakeRuntime(events=[{"type": "delta", "text": "半句"}],
                          default={"status": "success", "message": "不该出现"})
    emit = _sink()

    def abort_on_delta(event):
        emit(event)
        return False

    result = run_turn(FakeAgent(runtime=runtime), store, sid, "长任务", abort_on_delta)

    assert result["status"] == "aborted"
    messages = store.get_messages(sid)
    assert messages[-1]["content"] == "（已中断）"
    assert emit.collected[-1]["type"] == "done"
    assert "不该出现" not in messages[-1]["content"]


def test_context_carries_history_summary_and_assets(store):
    sid = store.create_session()
    store.append_message(sid, "user", "上一问")
    store.append_message(sid, "assistant", "上一答")
    store.update_session_meta(sid, summary="## 目标\n旧目标", summary_upto=1,
                              downloaded_assets=[{"access_path": "a.csv"}])
    runtime = FakeRuntime()

    run_turn(FakeAgent(runtime=runtime), store, sid, "继续", _sink())

    context = runtime.calls[0]["context"]
    assert context["summary"] == "## 目标\n旧目标"
    assert context["downloaded_assets"] == [{"access_path": "a.csv"}]
    assert context["last_user_input"] == "继续"
    # summary_upto=1 表示首条已摘要，recent 窗口仅含其后消息（即 assistant）
    assert [m["content"] for m in context["history"]] == ["上一答"]


def test_compaction_result_is_written_back_to_session(store):
    sid = store.create_session()
    for i in range(5):
        store.append_message(sid, "user", f"问{i}")
        store.append_message(sid, "assistant", f"答{i}")
    runtime = FakeRuntime()

    run_turn(FakeAgent(runtime=runtime), store, sid, "新问题", _sink())

    # 无 LLM 客户端时 maybe_compact 原样返回，summary 仍写回（不丢字段）
    session = store.get_session(sid)
    assert "summary" in session
    assert session["summary_upto"] == 0


def test_soft_warn_event_emitted_when_threshold_crossed(store, monkeypatch):
    from src.api import chat_service

    sid = store.create_session()
    store.append_message(sid, "user", "旧问")
    monkeypatch.setattr(chat_service.compact, "should_soft_warn",
                        lambda summary, window: True)
    emit = _sink()

    run_turn(FakeAgent(runtime=FakeRuntime()), store, sid, "继续", emit)

    assert {"type": "compressed", "soft_warn": True} in emit.collected


def test_session_lock_is_per_session_and_reentrant_lookup():
    first = session_lock("s1")
    assert session_lock("s1") is first
    assert session_lock("s2") is not first
    assert first.acquire(blocking=False) is True
    assert first.acquire(blocking=False) is False
    first.release()
    assert first.acquire(blocking=False) is True
    first.release()
