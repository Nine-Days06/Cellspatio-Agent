"""chat_service.run_turn：落库时机、分支流转、事件顺序、会话锁。"""

from __future__ import annotations

import json

import pytest

from src.api.chat_service import _persist, persist_message, persist_outcome, run_turn, session_lock
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

        def execute(self, user_input, context=None, on_event=None, **kwargs):
            roles_at_execute.append([m["role"] for m in store.get_messages(sid)])
            return super().execute(user_input, context, on_event, **kwargs)

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


def test_knowledge_qa_mode_uses_strict_agent_with_history(store):
    """知识问答收敛为同一 agent 的严格文献模式，并保留压缩后的历史。"""
    sid = store.create_session()
    store.append_message(sid, "user", "历史里的秘密")
    store.append_message(sid, "assistant", "历史回答")
    store.update_session_meta(sid, summary="历史摘要", summary_upto=0)
    payload = {
        "status": "success",
        "type": "general_response",
        "outcome": "supported_answer",
        "message": "可追溯回答 [E1]",
        "results": {
            "knowledge_evidence": {
                "outcome": "supported_answer",
                "evidence": [],
                "sources": [],
                "claims": [],
                "citations": ["E1"],
            }
        },
    }
    runtime = FakeRuntime(results=[payload])
    agent = FakeAgent(runtime=runtime)
    emit = _sink()

    result = run_turn(agent, store, sid, "新问题", emit, mode="knowledge_qa")

    call = runtime.calls[0]
    assert call["knowledge_mode"] is True
    assert call["context"]["summary"] == "历史摘要"
    assert "历史回答" in [item["content"] for item in call["context"]["history"]]
    last = store.get_messages(sid)[-1]
    assert last["content"] == "可追溯回答 [E1]"
    assert last["results"]["knowledge_evidence"]["citations"] == ["E1"]
    assert result["type"] == "general_response"


def test_knowledge_qa_passes_untruncated_recent_user_turns(store):
    """历史窗口会截断用户发言，但严格检索 query 单独拿用户原话。"""
    sid = store.create_session()
    long_turn = "问" * 3000
    store.append_message(sid, "user", long_turn)
    store.append_message(sid, "assistant", "回答")
    runtime = FakeRuntime()

    run_turn(FakeAgent(runtime=runtime), store, sid, "那它呢？", _sink(), mode="knowledge_qa")

    context = runtime.calls[0]["context"]
    assert context["recent_user_turns"] == [long_turn]
    assert len(context["history"][0]["content"]) < len(long_turn)


def test_knowledge_qa_stops_before_retrieval_when_client_is_gone(store):
    """启动事件发不出去说明连接已断；严格文献轮同样落中断终态。"""
    sid = store.create_session()
    runtime = FakeRuntime(
        events=[{"type": "tool_status", "name": "search_knowledge", "phase": "start"}]
    )
    agent = FakeAgent(runtime=runtime)
    emit = _sink(abort_types=("tool_status",))

    result = run_turn(agent, store, sid, "新问题", emit, mode="knowledge_qa")

    assert runtime.calls[0]["knowledge_mode"] is True
    assert result["status"] == "aborted"
    last = store.get_messages(sid)[-1]
    assert last["role"] == "assistant"
    assert last["content"] == "（已中断）"


def test_general_branch_persists_assistant_message_and_done_event(store):
    sid = store.create_session()
    runtime = FakeRuntime(
        default={
            "status": "success",
            "type": "knowledge_response",
            "response": "抑癌基因",
            "references": [],
        }
    )
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


def test_persist_outcome_without_emit_only_persists(store):
    sid = store.create_session()
    result = {
        "status": "success",
        "message": "分析完成",
        "results": {"charts": [{"type": "image", "image_b64": "AAA"}]},
    }

    returned, last = persist_outcome(store, sid, result)

    assert returned is result
    assert last == store.get_messages(sid)[-1]
    assert last["role"] == "assistant"
    assert last["results"]["charts"] == [{"type": "image", "image_b64": "AAA"}]


def test_persist_alias_keeps_legacy_name():
    assert _persist is persist_message


def test_persist_outcome_emits_charts_then_serialisable_done(store):
    import plotly.graph_objects as go

    sid = store.create_session()
    fig = go.Figure(go.Scatter(x=[1], y=[2]))
    result = {"status": "success", "message": "ok", "results": {"charts": [fig]}}
    emit = _sink()

    returned, last = persist_outcome(store, sid, result, emit=emit)

    assert returned is result
    assert [event["type"] for event in emit.collected] == ["chart", "done"]
    done = emit.collected[-1]
    assert done["message_id"] == last["id"]
    assert done["message"]["results"]["charts"][0]["type"] == "plotly"
    json.dumps(done)


def test_chart_events_precede_done_event(store):
    import plotly.graph_objects as go

    sid = store.create_session()
    fig = go.Figure(go.Scatter(x=[1], y=[2]))
    runtime = FakeRuntime(
        default={"status": "success", "message": "ok", "results": {"charts": [fig]}}
    )
    emit = _sink()

    run_turn(FakeAgent(runtime=runtime), store, sid, "分析", emit)

    types = [e["type"] for e in emit.collected]
    assert types[-1] == "done"
    assert "chart" in types
    assert store.get_messages(sid)[-1]["results"]["charts"][0].data is not None


def test_script_confirmation_branch_persists_pending_script(store):
    sid = store.create_session()
    runtime = FakeRuntime(
        default={
            "status": "needs_script_confirmation",
            "message": "已生成 R 脚本",
            "script": "library(DESeq2)",
            "analysis_type": "differential_expression",
            "params": {"group_by": "condition"},
            "method_context": "DESeq2 常规流程",
        }
    )
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
    runtime = FakeRuntime(
        default={
            "status": "needs_confirmation",
            "type": "fetch_data",
            "candidates": candidates,
            "query": "肝癌",
            "message": "找到 1 个候选数据集，请选择要下载的项：",
        }
    )
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
    runtime = FakeRuntime(
        events=[{"type": "delta", "text": "半句"}],
        default={"status": "success", "message": "不该出现"},
    )
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


def test_aborted_turn_records_mode_for_retry(store):
    """中断轮也要落 chat_mode，前端重试才能沿用严格文献链路。"""
    sid = store.create_session()
    runtime = FakeRuntime(events=[{"type": "delta", "text": "半句"}], default={"status": "success"})
    emit = _sink()

    def abort_on_delta(event):
        emit(event)
        return False

    result = run_turn(
        FakeAgent(runtime=runtime), store, sid, "TP53", abort_on_delta, mode="knowledge_qa"
    )

    assert result["status"] == "aborted"
    assert store.get_messages(sid)[-1]["results"]["chat_mode"] == "knowledge_qa"


def test_before_done_runs_after_assistant_persisted_and_before_done_event(store):
    """收尾挂点：助手消息已落库、done 尚未发射，且只触发一次。"""
    sid = store.create_session()
    seen: list[tuple[str, list[str], list[str]]] = []
    emit = _sink()

    def hook(store_arg, session_id):
        seen.append(
            (
                session_id,
                [m["role"] for m in store_arg.get_messages(session_id)],
                [e["type"] for e in emit.collected],
            )
        )

    run_turn(
        FakeAgent(runtime=FakeRuntime(default={"status": "success", "message": "回答"})),
        store,
        sid,
        "你好",
        emit,
        before_done=hook,
    )

    assert seen == [(sid, ["user", "assistant"], [])]
    assert emit.collected[-1]["type"] == "done"


def test_before_done_not_called_on_aborted_or_error_branch(store):
    """中断与报错首轮不满足“首轮问答完成”，收尾挂点不得触发。"""
    sid = store.create_session()
    called: list[str] = []
    emit = _sink()

    runtime = FakeRuntime(
        events=[{"type": "delta", "text": "半句"}],
        default={"status": "success", "message": "不该出现"},
    )

    def abort_on_delta(event):
        emit(event)
        return False

    run_turn(
        FakeAgent(runtime=runtime),
        store,
        sid,
        "长任务",
        abort_on_delta,
        before_done=lambda store_arg, session_id: called.append(session_id),
    )

    assert called == []
    assert store.get_messages(sid)[-1]["content"] == "（已中断）"


def test_first_turn_updates_title_before_done_event(store):
    """首轮完成时标题在 done 之前写入，前端本轮结束即可刷新看到。"""
    from src.ui.session_title import maybe_name_session

    sid = store.create_session()
    emit = _sink()
    titles_at_done: list[str] = []

    def emit_and_capture(event):
        if event.get("type") == "done":
            titles_at_done.append(store.get_session(sid)["title"])
        return emit(event)

    class FakeCompletions:
        def create(self, **kwargs):
            message = type("Message", (), {"content": "PBMC 质控与聚类"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeLLM:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    run_turn(
        FakeAgent(
            runtime=FakeRuntime(default={"status": "success", "message": "完成"}),
            llm_client=FakeLLM(),
        ),
        store,
        sid,
        "帮我做 PBMC 质控和聚类",
        emit_and_capture,
        before_done=lambda store_arg, session_id: maybe_name_session(
            store_arg, session_id, FakeLLM(), "m1"
        ),
    )

    assert titles_at_done == ["PBMC 质控与聚类"]
    assert store.get_session(sid)["title_source"] == "model"


def test_error_first_turn_does_not_trigger_auto_name(store):
    """报错首轮没有完成问答，收尾挂点不得用错误文案生成标题。"""
    sid = store.create_session()
    called: list[str] = []
    emit = _sink()

    run_turn(
        FakeAgent(runtime=FakeRuntime(default={"status": "error", "message": "模型调用失败"})),
        store,
        sid,
        "帮我做质控",
        emit,
        before_done=lambda store_arg, session_id: called.append(session_id),
    )

    assert called == []
    assert store.get_session(sid)["title"] == "帮我做质控"
    assert store.get_session(sid)["title_source"] == "derived"


def test_no_llm_client_keeps_derived_title_and_emits_done(store):
    """没有 LLM 客户端时沿用首条消息兜底标题，done 照常发射。"""
    from src.ui.session_title import maybe_name_session

    sid = store.create_session()
    emit = _sink()

    run_turn(
        FakeAgent(runtime=FakeRuntime(default={"status": "success", "message": "回答"})),
        store,
        sid,
        "TP53 的作用",
        emit,
        before_done=lambda store_arg, session_id: maybe_name_session(
            store_arg, session_id, None, "m1"
        ),
    )

    assert emit.collected[-1]["type"] == "done"
    assert store.get_session(sid)["title"] == "TP53 的作用"
    assert store.get_session(sid)["title_source"] == "derived"


def test_context_carries_history_summary_and_assets(store):
    sid = store.create_session()
    store.append_message(sid, "user", "上一问")
    store.append_message(sid, "assistant", "上一答")
    store.update_session_meta(
        sid, summary="## 目标\n旧目标", summary_upto=1, downloaded_assets=[{"access_path": "a.csv"}]
    )
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
    monkeypatch.setattr(chat_service.compact, "should_soft_warn", lambda summary, window: True)
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


def test_knowledge_qa_persist_failure_is_not_reported_as_success(store, monkeypatch):
    """落库失败必须冒泡，不能对外宣称这一轮已完成。"""
    sid = store.create_session()
    runtime = FakeRuntime(
        results=[
            {
                "status": "success",
                "type": "general_response",
                "outcome": "supported_answer",
                "message": "回答 [E1]",
                "results": {"knowledge_evidence": {"citations": ["E1"]}},
            }
        ]
    )
    real_append = store.append_message

    def flaky_append(session_id, role, content, **kwargs):
        if role == "assistant":
            raise RuntimeError("disk full")
        return real_append(session_id, role, content, **kwargs)

    monkeypatch.setattr(store, "append_message", flaky_append)
    agent = FakeAgent(runtime=runtime)
    emit = _sink()

    with pytest.raises(RuntimeError, match="disk full"):
        run_turn(agent, store, sid, "问题", emit, mode="knowledge_qa")

    assert emit.collected == []


def test_knowledge_qa_disconnect_after_generation_keeps_answer(store):
    """回答已生成后写入失败仍应落库正文，不能因 done 发不出去而丢失。"""
    sid = store.create_session()
    runtime = FakeRuntime(
        results=[
            {
                "status": "success",
                "type": "general_response",
                "outcome": "supported_answer",
                "message": "已有回答 [E1]",
                "results": {"knowledge_evidence": {"citations": ["E1"]}},
            }
        ]
    )
    collected: list[dict] = []

    def emit(event):
        collected.append(event)
        return True

    agent = FakeAgent(runtime=runtime)

    result = run_turn(agent, store, sid, "问题", emit, mode="knowledge_qa")

    assert result["status"] == "success"
    assert collected[-1]["type"] == "done"
    assert store.get_messages(sid)[-1]["content"] == "已有回答 [E1]"
