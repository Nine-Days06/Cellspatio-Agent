"""chat_service.run_turn：落库时机、分支流转、事件顺序、会话锁。"""

from __future__ import annotations

import json

import pytest

from src.api.chat_service import _persist, persist_message, persist_outcome, run_turn, session_lock
from src.knowledge.qa import interrupted_package
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


def test_knowledge_qa_mode_bypasses_history_runtime_and_compaction(store, monkeypatch):
    """显式问答只把本轮问题交给证据链，不压缩历史、不进入工具循环。"""
    sid = store.create_session()
    store.append_message(sid, "user", "历史里的秘密")
    store.append_message(sid, "assistant", "历史回答")
    store.update_session_meta(sid, summary="历史摘要", summary_upto=2)
    payload = {
        "answer_id": "qa-test",
        "schema_version": "knowledge-qa/2.0.0",
        "status": "success",
        "outcome": "supported_answer",
        "answer": "可追溯回答",
        "claims": [],
        "evidence": [],
        "sources": [],
    }
    seen: list[str] = []
    seen_kwargs: list[dict] = []

    def fake_run(client, question, **kwargs):
        seen.append(question)
        seen_kwargs.append(kwargs)
        on_stage = kwargs["on_stage"]
        for stage in ("retrieval", "answer"):
            on_stage(stage, "start")
            on_stage(stage, "end")
        return payload

    monkeypatch.setattr("src.knowledge.qa.run_knowledge_qa", fake_run)
    agent = FakeAgent()
    agent.knowledge_client = object()
    emit = _sink()

    result = run_turn(agent, store, sid, "新问题", emit, mode="knowledge_qa")

    assert seen == ["新问题"]
    user_message_id = [m for m in store.get_messages(sid) if m["role"] == "user"][-1]["id"]
    assert seen_kwargs[0]["session_id"] == sid
    assert seen_kwargs[0]["question_message_id"] == user_message_id
    assert callable(seen_kwargs[0]["on_stage"])
    assert agent.agent_runtime.calls == []
    assert store.get_session(sid)["summary"] == "历史摘要"
    last = store.get_messages(sid)[-1]
    assert last["content"] == "可追溯回答"
    assert last["results"]["knowledge_qa"]["answer_id"] == "qa-test"
    assert [(event["type"], event.get("name"), event.get("phase")) for event in emit.collected] == [
        ("tool_status", "knowledge_qa_retrieval", "start"),
        ("tool_status", "knowledge_qa_retrieval", "end"),
        ("tool_status", "knowledge_qa_answer", "start"),
        ("tool_status", "knowledge_qa_answer", "end"),
        ("done", None, None),
    ]
    assert emit.collected[0]["label"] == "检索知识库"
    assert emit.collected[2]["label"] == "生成回答"
    assert result["type"] == "knowledge_qa"


def test_knowledge_qa_stops_before_retrieval_when_client_is_gone(store, monkeypatch):
    """启动事件发不出去说明连接已断，不再发起检索/生成调用。"""
    sid = store.create_session()
    called: list[str] = []

    def fake_run(client, question, **kwargs):
        if kwargs["on_stage"]("retrieval", "start") is False:
            return interrupted_package(
                question,
                session_id=kwargs["session_id"],
                question_message_id=kwargs["question_message_id"],
            )
        called.append(question)
        return {}

    monkeypatch.setattr("src.knowledge.qa.run_knowledge_qa", fake_run)
    agent = FakeAgent()
    agent.knowledge_client = object()
    emit = _sink(abort_types=("tool_status",))

    result = run_turn(agent, store, sid, "新问题", emit, mode="knowledge_qa")

    assert called == []
    assert result["status"] == "interrupted"
    last = store.get_messages(sid)[-1]
    assert last["role"] == "assistant"
    assert last["results"]["knowledge_qa"]["status"] == "interrupted"
    assert (
        last["results"]["knowledge_qa"]["question_message_id"]
        == [m for m in store.get_messages(sid) if m["role"] == "user"][-1]["id"]
    )


def test_knowledge_qa_prompt_excludes_history_summary_and_assets(store, monkeypatch):
    """证据边界：模型只应看到本轮问题与本轮检索片段。"""
    sid = store.create_session()
    store.append_message(sid, "user", "历史里的秘密")
    store.append_message(sid, "assistant", "历史回答")
    store.update_session_meta(sid, summary="历史摘要", summary_upto=2)

    class Client:
        def query_data(self, question, mode="mix", **kwargs):
            return {
                "status": "success",
                "data": {
                    "chunks": [
                        {
                            "chunk_id": "c1",
                            "content": "标题：A paper\n摘要：Retrieved evidence.\nPMID：38947524",
                        }
                    ]
                },
                "metadata": {},
            }

    prompts: list[str] = []

    async def fake_llm(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps({"outcome": "insufficient_evidence", "answer": "证据不足"})

    monkeypatch.setattr("src.knowledge.qa.build_llm_func", lambda: (fake_llm, "fake-model"))
    agent = FakeAgent()
    agent.knowledge_client = Client()

    run_turn(agent, store, sid, "新问题", _sink(), mode="knowledge_qa")

    assert prompts, "生成阶段应当被调用"
    for sentinel in ("历史里的秘密", "历史回答", "历史摘要"):
        assert sentinel not in prompts[0]
    assert "Retrieved evidence." in prompts[0]


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

    def fake_run(client, question, **kwargs):
        on_stage = kwargs["on_stage"]
        for stage in ("retrieval", "answer"):
            on_stage(stage, "start")
            on_stage(stage, "end")
        return {
            "answer_id": "qa-test",
            "status": "success",
            "outcome": "supported_answer",
            "answer": "回答",
            "claims": [],
            "evidence": [],
            "sources": [],
        }

    monkeypatch.setattr("src.knowledge.qa.run_knowledge_qa", fake_run)
    real_append = store.append_message

    def flaky_append(session_id, role, content, **kwargs):
        if role == "assistant":
            raise RuntimeError("disk full")
        return real_append(session_id, role, content, **kwargs)

    monkeypatch.setattr(store, "append_message", flaky_append)
    agent = FakeAgent()
    agent.knowledge_client = object()
    emit = _sink()

    with pytest.raises(RuntimeError, match="disk full"):
        run_turn(agent, store, sid, "问题", emit, mode="knowledge_qa")

    assert [e["type"] for e in emit.collected] == ["tool_status"] * 4


def test_knowledge_qa_disconnect_after_generation_keeps_answer(store, monkeypatch):
    """生成完才发现断连：落 interrupted，但保留已产出的回答，且不发 done。"""
    sid = store.create_session()
    payload = {
        "answer_id": "qa-done",
        "status": "success",
        "outcome": "supported_answer",
        "answer": "已有回答",
        "claims": [],
        "evidence": [],
        "sources": [],
    }

    def fake_run(client, question, **kwargs):
        on_stage = kwargs["on_stage"]
        for stage in ("retrieval", "answer"):
            on_stage(stage, "start")
            on_stage(stage, "end")
        return payload

    monkeypatch.setattr("src.knowledge.qa.run_knowledge_qa", fake_run)
    collected: list[dict] = []
    seen: list[int] = []

    def emit(event):
        collected.append(event)
        if event["type"] == "tool_status":
            seen.append(1)
            return len(seen) == 1
        return True

    agent = FakeAgent()
    agent.knowledge_client = object()

    result = run_turn(agent, store, sid, "问题", emit, mode="knowledge_qa")

    assert result["status"] == "interrupted"
    assert [e["type"] for e in collected] == ["tool_status"] * 4
    pkg = store.get_messages(sid)[-1]["results"]["knowledge_qa"]
    assert pkg["status"] == "interrupted"
    assert pkg["answer"] == "已有回答"
    assert store.get_messages(sid)[-1]["content"] == "已有回答"
