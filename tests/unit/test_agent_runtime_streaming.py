"""AgentRuntime 流式路径：on_event 推送 delta/tool_status、tool_calls 跨 chunk 累积、协作式中止。"""
from __future__ import annotations

from types import SimpleNamespace

from tests.unit.test_agent_runtime import FakeWM, _text_response


def _text_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))]
    )


def _tool_chunk(index: int, call_id: str = "", name: str = "", arguments: str = "") -> SimpleNamespace:
    call = SimpleNamespace(
        index=index,
        id=call_id or None,
        function=SimpleNamespace(name=name or None, arguments=arguments or None),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))]
    )


class FakeStreamLLM:
    """流式桩：每轮按脚本产出 chunk 迭代器，记录每次 create 的 kwargs。"""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripts:
            raise AssertionError("FakeStreamLLM script queue exhausted")
        return iter(self._scripts.pop(0))


class EventSink:
    """事件收集器；abort_on 命中的事件类型让 emit 返回 False（模拟客户端断开）。"""

    def __init__(self, abort_on: str | None = None):
        self.events: list[dict] = []
        self.abort_on = abort_on

    def __call__(self, event: dict) -> bool:
        self.events.append(event)
        return not (self.abort_on and event.get("type") == self.abort_on)


def _runtime(llm, wm=None):
    from src.control.agent_runtime import AgentRuntime

    wm = wm or FakeWM({})
    return AgentRuntime(llm_client=llm, model="fake-model", workflow_manager=wm), wm


class ScriptedLLM:
    """非流式桩：按脚本返回固定响应对象，记录每次 create 的 kwargs。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("ScriptedLLM response queue exhausted")
        return self._responses.pop(0)


def test_non_stream_call_passes_no_stream_kwarg():
    """on_event=None 时 create 不带 stream 键（与现有行为逐字节等价）。"""
    llm = ScriptedLLM([_text_response("你好")])
    runtime, wm = _runtime(llm)

    result = runtime.execute("你好")

    assert result["message"] == "你好"
    assert "stream" not in llm.calls[0]
    assert llm.calls[0]["tool_choice"] == "auto"
    assert wm.calls == []


def test_stream_delta_events_arrive_in_order():
    """流式无工具轮：delta 按 chunk 顺序推送，最终消息为累积内容。"""
    llm = FakeStreamLLM([[_text_chunk("你"), _text_chunk("好"), _text_chunk("呀")]])
    runtime, wm = _runtime(llm)
    sink = EventSink()

    result = runtime.execute("你好", on_event=sink)

    assert sink.events == [
        {"type": "delta", "text": "你"},
        {"type": "delta", "text": "好"},
        {"type": "delta", "text": "呀"},
    ]
    assert result == {"status": "success", "type": "general_response", "message": "你好呀"}
    assert llm.calls[0]["stream"] is True
    assert wm.calls == []


def test_stream_tool_calls_accumulate_across_chunks_then_dispatch():
    """工具名与 arguments 分块到达时按 index 拼接，能正确 dispatch 并进入第二轮。"""
    llm = FakeStreamLLM([
        [
            _tool_chunk(0, "call_1", "query_knowledge", '{"que'),
            _tool_chunk(0, arguments='ry": "TP53 的作用"}'),
        ],
        [_text_chunk("抑癌基因")],
    ])
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)
    sink = EventSink()

    result = runtime.execute("TP53 的作用是什么？", on_event=sink)

    assert wm.calls == [("query_knowledge", {"query": "TP53 的作用"})]
    assert result["message"] == "抑癌基因"
    assert [e["type"] for e in sink.events] == ["tool_status", "tool_status", "delta"]
    assert sink.events[0] == {"type": "tool_status", "name": "query_knowledge", "phase": "start"}
    assert sink.events[1] == {"type": "tool_status", "name": "query_knowledge", "phase": "end"}


def test_stream_tool_message_history_carries_assembled_tool_call():
    """tool 结果回灌历史时，assistant 消息的 tool_calls 形状与非流式一致。"""
    llm = FakeStreamLLM([
        [_tool_chunk(0, "call_9", "query_knowledge", '{"query": "x"}')],
        [_text_chunk("done")],
    ])
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)

    runtime.execute("问个问题", on_event=EventSink())

    second_round = llm.calls[1]["messages"]
    assert second_round[-2]["role"] == "assistant"
    assert second_round[-2]["tool_calls"][0]["id"] == "call_9"
    assert second_round[-2]["tool_calls"][0]["function"] == {
        "name": "query_knowledge", "arguments": '{"query": "x"}',
    }
    assert second_round[-1]["tool_call_id"] == "call_9"


def test_abort_on_delta_returns_aborted_result():
    """delta 阶段 emit 返回 False：立即返回 aborted，不再跑后续轮。"""
    llm = FakeStreamLLM([[_text_chunk("半"), _text_chunk("句")]])
    runtime, _ = _runtime(llm)

    result = runtime.execute("继续", on_event=EventSink(abort_on="delta"))

    assert result == {"status": "aborted", "type": "general_response", "message": "已中断"}
    assert len(llm.calls) == 1


def test_abort_on_tool_status_start_skips_dispatch():
    """tool_status start 返回 False：工具不得被执行（客户端已断开）。"""
    llm = FakeStreamLLM([[_tool_chunk(0, "c1", "run_analysis", '{"analysis_type": "single_cell"}')]])
    wm = FakeWM({"run_analysis": {"status": "success", "message": "ok"}})
    runtime, wm = _runtime(llm, wm)
    sink = EventSink(abort_on="tool_status")

    result = runtime.execute("做聚类", on_event=sink)

    assert result["status"] == "aborted"
    assert wm.calls == []
    assert len(sink.events) == 1


def test_tool_status_end_emit_result_is_ignored():
    """phase=end 的 emit 返回值被忽略：工作照常进入下一轮（已产出工具结果不能丢）。"""
    class EndOnlySink(EventSink):
        def __call__(self, event: dict) -> bool:
            self.events.append(event)
            return event.get("phase") != "end"

    llm = FakeStreamLLM([
        [_tool_chunk(0, "c1", "query_knowledge", '{"query": "y"}')],
        [_text_chunk("答")],
    ])
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, wm = _runtime(llm, wm)
    sink = EndOnlySink()

    result = runtime.execute("问", on_event=sink)

    assert wm.calls == [("query_knowledge", {"query": "y"})]
    assert result["message"] == "答"
    assert [e.get("phase") for e in sink.events] == ["start", "end"]


def test_stream_ignores_chunks_without_choices():
    """usage-only 等无 choices 的 chunk 直接跳过，不炸。"""
    llm = FakeStreamLLM([
        [SimpleNamespace(choices=[]), _text_chunk("好")],
    ])
    runtime, _ = _runtime(llm)
    sink = EventSink()

    result = runtime.execute("q", on_event=sink)

    assert result["message"] == "好"
    assert len(sink.events) == 1


def test_stream_create_exception_falls_back_to_legacy_workflow():
    """流式 create 抛异常：沿用既有 try/except 回退 WorkflowManager.execute_workflow。"""

    class BoomLLM:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("api down")

    runtime, wm = _runtime(BoomLLM())

    result = runtime.execute("做差异表达", on_event=EventSink())

    assert wm.fallback_calls == ["做差异表达"]
    assert result["message"] == "fallback"


def test_stream_rounds_capped_at_max_rounds():
    """持续请求工具时按 MAX_ROUNDS 收敛，返回既有上限错误文案。"""
    from src.control.agent_runtime import MAX_ROUNDS

    llm = FakeStreamLLM([
        [_tool_chunk(0, f"c{i}", "query_knowledge", '{"query": "z"}')]
        for i in range(MAX_ROUNDS)
    ])
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("一直问", on_event=EventSink())

    assert result["status"] == "error"
    assert "已达最大工具调用轮数" in result["message"]
    assert len(llm.calls) == MAX_ROUNDS