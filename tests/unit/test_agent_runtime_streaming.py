"""AgentRuntime 流式路径：on_event 推送 delta/tool_status、tool_calls 跨 chunk 累积、协作式中止。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.unit.test_agent_runtime import FakeWM, _text_response, _tool_call_response


@pytest.fixture(autouse=True)
def _legacy_knowledge_tool(monkeypatch):
    """既有流式用例验证旧链路；文献工具用例自行覆盖为 search。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "legacy")


def _text_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))]
    )


def _tool_chunk(
    index: int, call_id: str = "", name: str = "", arguments: str = ""
) -> SimpleNamespace:
    call = SimpleNamespace(
        index=index,
        id=call_id or None,
        function=SimpleNamespace(name=name or None, arguments=arguments or None),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))]
    )


def _tool_chunk_no_index(call_id: str = "", name: str = "", arguments: str = "") -> SimpleNamespace:
    """工具调用 chunk 但不带 index 字段（模拟某些 SDK 可能省略的情况）。"""
    call = SimpleNamespace(
        id=call_id or None,
        function=SimpleNamespace(name=name or None, arguments=arguments or None),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))]
    )


def _reasoning_chunk(text: str, *, field: str = "reasoning_content") -> SimpleNamespace:
    delta = SimpleNamespace(content=None, tool_calls=None)
    setattr(delta, field, text)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


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


@pytest.mark.parametrize("status", ["needs_confirmation", "needs_input", "error"])
def test_qc_is_terminal_without_sending_results_to_llm(status):
    """成功、缺输入、失败都停住，不将本地证据回灌模型。"""
    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "qc1", "run_analysis", '{"analysis_type":"single_cell"}')],
        ]
    )
    evidence = {
        "status": status,
        "analysis_type": "single_cell",
        "stage": "qc_review",
        "results": {"qc_artifacts": {"x": "PRIVATE_PATH"}},
    }
    runtime, _ = _runtime(llm, FakeWM({"run_analysis": evidence}))
    assert runtime.execute("单细胞 RNA 分析", on_event=EventSink()) == evidence
    assert len(llm.calls) == 1
    assert "PRIVATE_PATH" not in str(llm.calls)


def test_qc_terminal_still_closes_tool_status():
    """单细胞直接返回时也必须发 tool_status end，否则流式过程条不结束。"""
    llm = FakeStreamLLM(
        [[_tool_chunk(0, "qc1", "run_analysis", '{"analysis_type":"single_cell"}')]]
    )
    evidence = {"status": "needs_input", "analysis_type": "single_cell"}
    runtime, _ = _runtime(llm, FakeWM({"run_analysis": evidence}))
    sink = EventSink()

    assert runtime.execute("单细胞 RNA 分析", on_event=sink) == evidence
    assert [e.get("phase") for e in sink.events if e.get("type") == "tool_status"] == [
        "start",
        "end",
    ]
    assert len(llm.calls) == 1


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
    llm = FakeStreamLLM(
        [
            [
                _tool_chunk(0, "call_1", "query_knowledge", '{"que'),
                _tool_chunk(0, arguments='ry": "TP53 的作用"}'),
            ],
            [_text_chunk("抑癌基因")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)
    sink = EventSink()

    result = runtime.execute("TP53 的作用是什么？", on_event=sink)

    assert wm.calls == [("query_knowledge", {"query": "TP53 的作用"})]
    assert result["message"] == "抑癌基因"
    assert [e["type"] for e in sink.events] == ["tool_status", "tool_status", "delta"]
    assert sink.events[0] == {
        "type": "tool_status",
        "name": "query_knowledge",
        "phase": "start",
    }
    assert sink.events[1] == {
        "type": "tool_status",
        "name": "query_knowledge",
        "phase": "end",
    }


def test_stream_tool_message_history_carries_assembled_tool_call():
    """tool 结果回灌历史时，assistant 消息的 tool_calls 形状与非流式一致。"""
    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "call_9", "query_knowledge", '{"query": "x"}')],
            [_text_chunk("done")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)

    runtime.execute("问个问题", on_event=EventSink())

    second_round = llm.calls[1]["messages"]
    assert second_round[-2]["role"] == "assistant"
    assert second_round[-2]["tool_calls"][0]["id"] == "call_9"
    assert second_round[-2]["tool_calls"][0]["function"] == {
        "name": "query_knowledge",
        "arguments": '{"query": "x"}',
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
    llm = FakeStreamLLM(
        [[_tool_chunk(0, "c1", "run_analysis", '{"analysis_type": "single_cell"}')]]
    )
    wm = FakeWM({"run_analysis": {"status": "success", "message": "ok"}})
    runtime, wm = _runtime(llm, wm)
    sink = EventSink(abort_on="tool_status")

    result = runtime.execute("做聚类", on_event=sink)

    assert result["status"] == "aborted"
    assert wm.calls == []
    assert len(sink.events) == 1


def test_tool_status_end_disconnect_aborts_before_next_model_call():
    """phase=end 写失败说明客户端已断开：结果照常回灌，但不得再发起模型调用。"""

    class EndOnlySink(EventSink):
        def __call__(self, event: dict) -> bool:
            self.events.append(event)
            return event.get("phase") != "end"

    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "c1", "query_knowledge", '{"query": "y"}')],
            [_text_chunk("答")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, wm = _runtime(llm, wm)
    sink = EndOnlySink()

    result = runtime.execute("问", on_event=sink)

    assert wm.calls == [("query_knowledge", {"query": "y"})]
    assert result["status"] == "aborted"
    assert len(llm.calls) == 1
    assert [e.get("phase") for e in sink.events if e.get("type") == "tool_status"] == [
        "start",
        "end",
    ]


def test_stream_ignores_chunks_without_choices():
    """usage-only 等无 choices 的 chunk 直接跳过，不炸。"""
    llm = FakeStreamLLM(
        [
            [SimpleNamespace(choices=[]), _text_chunk("好")],
        ]
    )
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


def test_stream_has_no_fixed_round_cap():
    """不设固定轮数：连续五轮不同参数的工具调用后仍能正常收口。"""
    llm = FakeStreamLLM(
        [[_tool_chunk(0, f"c{i}", "query_knowledge", f'{{"query": "q{i}"}}')] for i in range(5)]
        + [[_text_chunk("完成")]]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("一直问", on_event=EventSink())

    assert result["message"] == "完成"
    assert len(wm.calls) == 5


def test_consecutive_duplicate_tool_call_is_not_dispatched_again():
    """同一工具 + 同一参数连续重复：不再执行副作用，禁用工具让模型收口。"""
    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "c1", "query_knowledge", '{"query": "TP53"}')],
            [_tool_chunk(0, "c2", "query_knowledge", '{"query": "TP53"}')],
            [_text_chunk("基于已有结果回答")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("TP53 是什么", on_event=EventSink())

    assert len(wm.calls) == 1
    assert llm.calls[2]["tool_choice"] == "none"
    assert result["message"] == "基于已有结果回答"


def test_tool_result_is_truncated_to_per_call_cap(monkeypatch):
    """单次工具结果超过上限时截断并标注，避免撑爆上下文。"""
    monkeypatch.setenv("AGENT_TOOL_RESULT_MAX_CHARS", "160")
    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "c1", "query_knowledge", '{"query": "x"}')],
            [_text_chunk("答")],
        ]
    )
    wm = FakeWM(
        {
            "query_knowledge": {
                "status": "success",
                "continue_tool_loop": True,
                "message": "长" * 500,
            }
        }
    )
    runtime, _ = _runtime(llm, wm)

    runtime.execute("问", on_event=EventSink())

    payload = llm.calls[1]["messages"][-1]["content"]
    parsed = json.loads(payload)
    assert parsed["truncated"] is True
    assert "已截断" in parsed["note"]
    assert parsed["preview"]
    assert len(payload) <= 160


def test_tool_budget_exhaustion_switches_to_final_answer(monkeypatch):
    """累计结果预算用尽后禁用工具，不再执行新工具。"""
    monkeypatch.setenv("AGENT_TOOL_RESULT_BUDGET_CHARS", "80")
    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "c1", "query_knowledge", '{"query": "x"}')],
            [_text_chunk("基于已有信息回答")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "长" * 500}})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("问", on_event=EventSink())

    assert len(wm.calls) == 1
    assert llm.calls[1]["tool_choice"] == "none"
    assert result["message"] == "基于已有信息回答"


def test_continue_tool_loop_flag_overrides_success_terminal_status():
    """证据类工具即使 status=success 也继续主模型推理，而不是提前返回。"""
    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "c1", "query_knowledge", '{"query": "x"}')],
            [_text_chunk("整合后的回答")],
        ]
    )
    evidence = {
        "status": "success",
        "type": "knowledge_evidence",
        "evidence": [{"id": "E1"}],
        "continue_tool_loop": True,
    }
    wm = FakeWM({"query_knowledge": evidence})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("问", on_event=EventSink())

    assert len(llm.calls) == 2
    assert result["message"] == "整合后的回答"


def test_non_stream_duplicate_call_is_not_dispatched_twice():
    """非流式路径同样拦截重复调用，并强制模型基于已有结果收口。"""
    llm = ScriptedLLM(
        [
            _tool_call_response("query_knowledge", {"query": "TP53"}),
            _tool_call_response("query_knowledge", {"query": "TP53"}),
            _text_response("已有结果"),
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "success", "continue_tool_loop": True}})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("TP53 是什么")

    assert len(wm.calls) == 1
    assert llm.calls[2]["tool_choice"] == "none"
    assert result["message"] == "已有结果"


def test_non_stream_continues_past_three_tool_rounds():
    """非流式循环也不再受固定三轮限制。"""
    llm = ScriptedLLM(
        [_tool_call_response("query_knowledge", {"query": f"q{i}"}) for i in range(5)]
        + [_text_response("完成")]
    )
    wm = FakeWM({"query_knowledge": {"status": "success", "continue_tool_loop": True}})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("一直问")

    assert len(wm.calls) == 5
    assert result["message"] == "完成"


def test_forced_answer_that_still_requests_tool_fails_safely(monkeypatch):
    """预算耗尽后模型仍返回 tool_calls：不得执行新工具，返回安全失败。"""
    monkeypatch.setenv("AGENT_TOOL_RESULT_BUDGET_CHARS", "80")
    llm = ScriptedLLM(
        [
            _tool_call_response("query_knowledge", {"query": "x"}),
            _tool_call_response("query_knowledge", {"query": "y"}),
        ]
    )
    wm = FakeWM(
        {
            "query_knowledge": {
                "status": "success",
                "continue_tool_loop": True,
                "message": "长" * 500,
            }
        }
    )
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("问")

    assert len(wm.calls) == 1
    assert result["status"] == "error"
    assert "收口阶段" in result["message"]


def test_tool_signature_normalizes_key_order_and_tolerates_bad_json():
    """重复检测按规范化参数比较：键序不同视为同一调用，坏 JSON 不崩溃。"""
    from src.control.agent_runtime import AgentRuntime

    def call(arguments: str) -> SimpleNamespace:
        return SimpleNamespace(
            function=SimpleNamespace(name="query_knowledge", arguments=arguments)
        )

    assert AgentRuntime._tool_signature(call('{"a": 1, "b": 2}')) == (
        AgentRuntime._tool_signature(call('{"b": 2, "a": 1}'))
    )
    assert AgentRuntime._tool_signature(call("not-json")) == "query_knowledge\x00not-json"


def test_search_knowledge_status_reports_evidence_count(monkeypatch):
    """检索工具过程条结束事件附带证据条数；label 由 SSE 层统一补。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeStreamLLM(
        [
            [_tool_chunk(0, "c1", "search_knowledge", '{"query": "TP53"}')],
            [_text_chunk("答 [E1][E2]")],
        ]
    )
    wm = FakeWM(
        {
            "search_knowledge": {
                "status": "success",
                "type": "knowledge_evidence",
                "evidence": [{"evidence_id": "E1"}, {"evidence_id": "E2"}],
                "sources": [],
                "evidence_count": 2,
                "continue_tool_loop": True,
            }
        }
    )
    runtime, _ = _runtime(llm, wm)
    sink = EventSink()

    runtime.execute("TP53 的证据", on_event=sink)

    status_events = [e for e in sink.events if e.get("type") == "tool_status"]
    assert status_events[0] == {
        "type": "tool_status",
        "name": "search_knowledge",
        "phase": "start",
    }
    assert "label" not in status_events[0]
    assert status_events[1]["phase"] == "end"
    assert status_events[1]["count"] == 2
    assert "label" not in status_events[1]
    deltas = [event for event in sink.events if event.get("type") == "delta"]
    assert "".join(event["text"] for event in deltas) == "答 [E1][E2]"


def test_knowledge_mode_buffers_plain_answer_to_one_delta(monkeypatch):
    """开关开启时正文统一在轮次收口后发射，避免工具轮正文先泄露。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeStreamLLM([[_text_chunk("普通"), _text_chunk("回答")]])
    runtime, _ = _runtime(llm, FakeWM({}))
    sink = EventSink()

    result = runtime.execute("你好", on_event=sink)

    deltas = [event for event in sink.events if event.get("type") == "delta"]
    assert [event["text"] for event in deltas] == ["普通回答"]
    assert result["message"] == "普通回答"


def test_stream_tool_calls_accumulate_with_missing_index():
    """工具调用 chunk 缺失 index 时，按顺序归入当前槽或新建槽，不串槽。

    场景：首个 chunk 无 index，后续 chunk 有 index=0；或多个工具调用混合且部分无 index。
    语义：无 index 的 chunk 归入当前正在累积的槽（按出现顺序），若无当前槽则归入槽 0。
    """
    # 情况 1：首个 chunk 无 index，后续 chunk 有 index=0 —— 应归入同一槽
    llm = FakeStreamLLM(
        [
            [
                _tool_chunk_no_index("call_1", "query_knowledge", '{"que'),
                _tool_chunk(0, arguments='ry": "TP53"}'),
            ],
            [_text_chunk("抑癌基因")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)

    result = runtime.execute("问", on_event=EventSink())

    assert wm.calls == [("query_knowledge", {"query": "TP53"})]
    assert result["message"] == "抑癌基因"

    # 情况 2：单工具调用，所有 chunk 均无 index —— 应归入槽 0，arguments 拼接
    # 模拟真实协议：arguments 分块传输，而非重复
    llm2 = FakeStreamLLM(
        [
            [
                _tool_chunk_no_index("call_a", "query_knowledge", '{"query": "'),
                _tool_chunk_no_index(arguments='TP53"}'),
            ],
            [_text_chunk("done")],
        ]
    )
    wm2 = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime2, _ = _runtime(llm2, wm2)

    result2 = runtime2.execute("问", on_event=EventSink())

    # 两个 chunk 合并为一个工具调用，arguments 拼接为完整 JSON
    assert wm2.calls == [("query_knowledge", {"query": "TP53"})]
    assert result2["message"] == "done"


def test_stream_reasoning_chunks_are_tagged_and_not_mixed_into_content():
    """思考增量单独打 source=reasoning，正文只保留 content 累积。"""
    llm = FakeStreamLLM([[_reasoning_chunk("先想"), _reasoning_chunk("再想"), _text_chunk("答案")]])
    runtime, _ = _runtime(llm)
    sink = EventSink()

    result = runtime.execute("问题", on_event=sink)

    assert sink.events == [
        {"type": "delta", "text": "先想", "source": "reasoning"},
        {"type": "delta", "text": "再想", "source": "reasoning"},
        {"type": "delta", "text": "答案"},
    ]
    assert result["message"] == "答案"


def test_stream_accepts_reasoning_alias_and_ignores_non_string():
    """兼容 reasoning 字段；非字符串思考载荷不得进入事件流。"""
    bad = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=None, tool_calls=None, reasoning_content=123)
            )
        ]
    )
    llm = FakeStreamLLM([[_reasoning_chunk("别名", field="reasoning"), bad, _text_chunk("答")]])
    runtime, _ = _runtime(llm)
    sink = EventSink()

    runtime.execute("问题", on_event=sink)

    assert sink.events == [
        {"type": "delta", "text": "别名", "source": "reasoning"},
        {"type": "delta", "text": "答"},
    ]


def test_stream_reasoning_between_tool_rounds_is_separated():
    """工具调用前后的思考按顺序拼接，轮与轮之间插入轻量分隔。"""
    llm = FakeStreamLLM(
        [
            [_reasoning_chunk("第一轮"), _tool_chunk(0, "c1", "query_knowledge", '{"query": "x"}')],
            [_reasoning_chunk("第二轮"), _text_chunk("回答")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)
    sink = EventSink()

    result = runtime.execute("问", on_event=sink)

    assert [e for e in sink.events if e.get("source") == "reasoning"] == [
        {"type": "delta", "text": "第一轮", "source": "reasoning"},
        {"type": "delta", "text": "\n\n第二轮", "source": "reasoning"},
    ]
    assert result["message"] == "回答"


def test_reasoning_separator_survives_intermediate_round_without_reasoning():
    """中间轮没有思考时，后续再出现思考仍要与第一轮分隔。"""
    llm = FakeStreamLLM(
        [
            [_reasoning_chunk("第一轮"), _tool_chunk(0, "c1", "query_knowledge", '{"query": "x"}')],
            [_tool_chunk(0, "c2", "query_knowledge", '{"query": "y"}')],
            [_reasoning_chunk("第三轮"), _text_chunk("回答")],
        ]
    )
    wm = FakeWM({"query_knowledge": {"status": "needs_input", "message": "缺输入"}})
    runtime, _ = _runtime(llm, wm)
    sink = EventSink()

    runtime.execute("问", on_event=sink)

    assert [e["text"] for e in sink.events if e.get("source") == "reasoning"] == [
        "第一轮",
        "\n\n第三轮",
    ]


def test_abort_on_reasoning_delta_stops_before_content():
    """思考增量写不出去时立即中止本轮，不再继续读正文。"""

    class ReasoningAbort(EventSink):
        def __call__(self, event: dict) -> bool:
            self.events.append(event)
            return event.get("source") != "reasoning"

    llm = FakeStreamLLM([[_reasoning_chunk("想"), _text_chunk("不该出现")]])
    runtime, _ = _runtime(llm)
    sink = ReasoningAbort()

    result = runtime.execute("问", on_event=sink)

    assert result["status"] == "aborted"
    assert [e.get("text") for e in sink.events] == ["想"]
