"""AgentRuntime: tool-calling dispatch, HITL short-circuit, no-tool direct, legacy fallback."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _knowledge_tool_default_off(monkeypatch):
    """系统提示与工具清单断言基于默认关闭状态，避免开发环境 .env 干扰。"""
    monkeypatch.delenv("AGENT_KNOWLEDGE_TOOL", raising=False)


def _tool_call_response(name: str, arguments: dict) -> SimpleNamespace:
    tc = SimpleNamespace(
        id=f"call_{name}",
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc]))]
    )


def _text_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    )


class FakeLLM:
    """Returns pre-set responses in queue, records each create kwargs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeLLM response queue exhausted")
        return self._responses.pop(0)


class FakeWM:
    """WorkflowManager surface: records tool landings, returns pre-set results."""

    def __init__(self, results: dict):
        self.results = results
        self.calls: list[tuple[str, dict]] = []
        self.fallback_calls: list[str] = []

    def run_analysis_for_agent(self, analysis_type, params, context):
        self.calls.append(("run_analysis", {"analysis_type": analysis_type, "params": params}))
        return self.results["run_analysis"]

    def search_datasets_for_agent(self, query, params, context):
        self.calls.append(("search_datasets", {"query": query, "params": params}))
        return self.results["search_datasets"]

    def query_knowledge_for_agent(self, query, context):
        self.calls.append(("query_knowledge", {"query": query}))
        return self.results["query_knowledge"]

    def search_knowledge_for_agent(self, query, context):
        self.calls.append(("search_knowledge", {"query": query}))
        result = self.results["search_knowledge"]
        return result.pop(0) if isinstance(result, list) else result

    def query_memory_for_agent(self, query, context):
        self.calls.append(("query_memory", {"query": query}))
        return self.results["query_memory"]

    def execute_workflow(self, user_input, context=None):
        self.fallback_calls.append(user_input)
        return {"status": "success", "type": "general_response", "message": "fallback"}


def _make_runtime(llm, wm=None):
    from src.control.agent_runtime import AgentRuntime

    wm = wm or FakeWM({})
    return AgentRuntime(llm_client=llm, model="fake-model", workflow_manager=wm), wm


def test_dispatches_run_analysis_and_returns_terminal_result():
    llm = FakeLLM(
        [
            _tool_call_response(
                "run_analysis", {"analysis_type": "differential_expression", "question": "做差异"}
            )
        ]
    )
    wm = FakeWM(
        {
            "run_analysis": {
                "status": "success",
                "analysis_type": "differential_expression",
                "message": "ok",
                "results": {},
            }
        }
    )
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute(
        "对这份数据做差异表达分析", context={"downloaded_assets": [{"access_path": "a.csv"}]}
    )

    assert result["status"] == "success"
    assert wm.calls[0][0] == "run_analysis"
    assert wm.calls[0][1]["analysis_type"] == "differential_expression"
    sent = llm.calls[0]["messages"]
    assert sent[-1]["role"] == "user"
    assert "tools" in llm.calls[0]


def test_hitl_needs_confirmation_short_circuits_without_second_llm_call():
    llm = FakeLLM([_tool_call_response("search_datasets", {"query": "GSE123456"})])
    wm = FakeWM(
        {
            "search_datasets": {
                "status": "needs_confirmation",
                "type": "fetch_data",
                "candidates": [],
                "message": "选一个",
            }
        }
    )
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute("帮我下载 GSE123456")

    assert result["status"] == "needs_confirmation"
    assert len(llm.calls) == 1  # No second summary round


def test_no_tool_call_returns_general_message():
    llm = FakeLLM([_text_response("你好，有什么可以帮你？")])
    runtime, wm = _make_runtime(llm)

    result = runtime.execute("你好")

    assert result["status"] == "success"
    assert result["type"] == "general_response"
    assert "你好" in result["message"]
    assert wm.calls == []


def test_history_from_context_is_sent_to_llm():
    llm = FakeLLM([_text_response("ok")])
    runtime, _ = _make_runtime(llm)
    history = [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
    ]

    runtime.execute("继续", context={"history": history})

    assert "上一问" in [m["content"] for m in llm.calls[0]["messages"] if m.get("role") == "user"]


def test_no_llm_falls_back_to_workflow_manager():
    runtime, wm = _make_runtime(None)
    result = runtime.execute("做差异表达")
    assert wm.fallback_calls == ["做差异表达"]
    assert result["message"] == "fallback"


def test_llm_exception_falls_back_to_workflow_manager():
    class BoomLLM:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("api down")

    runtime, wm = _make_runtime(BoomLLM())
    result = runtime.execute("做差异表达")
    assert wm.fallback_calls == ["做差异表达"]
    assert result["status"] == "success"


def test_exception_after_dispatch_does_not_rerun_workflow():
    """工具已执行后 LLM 再失败，不得回退旧工作流重复副作用。"""

    class FailsAfterFirst(FakeLLM):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _tool_call_response(
                    "run_analysis",
                    {"analysis_type": "differential_expression", "question": "做差异"},
                )
            raise RuntimeError("api down")

    wm = FakeWM(
        {
            "run_analysis": {
                "status": "partial_failure",
                "type": "analysis",
                "message": "需要输入",
                "results": {"output_file": "a.csv"},
            }
        }
    )
    runtime, wm = _make_runtime(FailsAfterFirst([]), wm)

    result = runtime.execute("做差异表达")

    assert wm.calls and not wm.fallback_calls
    assert result["status"] == "error"
    assert result["results"] == {"output_file": "a.csv"}
    assert "请勿直接重复执行" in result["message"]


def test_query_knowledge_dispatch():
    llm = FakeLLM([_tool_call_response("query_knowledge", {"query": "TP53 在癌症中的作用"})])
    wm = FakeWM(
        {
            "query_knowledge": {
                "status": "success",
                "type": "knowledge_response",
                "response": "抑癌",
                "references": [],
            }
        }
    )
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute("TP53 在癌症中的作用是什么？")

    assert wm.calls == [("query_knowledge", {"query": "TP53 在癌症中的作用"})]
    assert result["type"] == "knowledge_response"


def test_query_memory_dispatch():
    """query_memory 工具调用分发到 workflow_manager，返回记忆问答终态。"""
    llm = FakeLLM([_tool_call_response("query_memory", {"query": "上次差异表达用的什么参数"})])
    wm = FakeWM(
        {
            "query_memory": {
                "status": "success",
                "type": "memory_response",
                "response": "run-abc123 使用 DESeq2，FDR=0.05",
            }
        }
    )
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute("上次那个差异表达分析用的什么参数？")

    assert wm.calls == [("query_memory", {"query": "上次差异表达用的什么参数"})]
    assert result["type"] == "memory_response"
    assert result["status"] == "success"


def test_search_knowledge_hidden_and_rejected_when_flag_off(monkeypatch):
    """默认关闭：工具清单不暴露，模型硬调用也只得到未知工具错误。"""
    monkeypatch.delenv("AGENT_KNOWLEDGE_TOOL", raising=False)
    from src.control.tools import build_tool_schemas

    assert "search_knowledge" not in {schema["function"]["name"] for schema in build_tool_schemas()}
    llm = FakeLLM([_tool_call_response("search_knowledge", {"query": "TP53"})])
    runtime, wm = _make_runtime(llm)

    result = runtime.execute("TP53 的证据")

    assert wm.calls == []
    assert result["status"] == "error"
    assert "未知工具" in result["message"]


def test_search_knowledge_dispatches_and_continues_round(monkeypatch):
    """开启后：schema 暴露、证据结果非终态，模型据此继续回答。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "TP53"}),
            _text_response("基于 [E1] 的回答"),
        ]
    )
    wm = FakeWM(
        {
            "search_knowledge": {
                "status": "success",
                "type": "knowledge_evidence",
                "evidence": [{"evidence_id": "E1", "content": "x"}],
                "sources": [],
                "evidence_count": 1,
                "continue_tool_loop": True,
            }
        }
    )
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute("TP53 的证据")

    assert wm.calls == [("search_knowledge", {"query": "TP53"})]
    assert "search_knowledge" in {schema["function"]["name"] for schema in llm.calls[0]["tools"]}
    assert "[E#]" in llm.calls[0]["messages"][0]["content"]
    assert result["message"] == "基于 [E1] 的回答"
    assert result["outcome"] == "supported_answer"


def test_evidence_ids_continue_across_multiple_tool_calls(monkeypatch):
    """同一轮两次检索的 E 编号接续（E1 → E2），不跨调用重号。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    evidences = [
        {
            "status": "success",
            "type": "knowledge_evidence",
            "evidence": [{"evidence_id": "E1", "chunk_id": "c1", "content": "x"}],
            "sources": [{"source_id": "s1", "evidence_ids": ["E1"]}],
            "evidence_count": 1,
            "continue_tool_loop": True,
        },
        {
            "status": "success",
            "type": "knowledge_evidence",
            "evidence": [{"evidence_id": "E1", "chunk_id": "c2", "content": "y"}],
            "sources": [{"source_id": "s2", "evidence_ids": ["E1"]}],
            "evidence_count": 1,
            "continue_tool_loop": True,
        },
    ]
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "q1"}),
            _tool_call_response("search_knowledge", {"query": "q2"}),
            _text_response("见 [E1] [E2]"),
        ]
    )
    runtime, _ = _make_runtime(llm, FakeWM({"search_knowledge": evidences}))

    runtime.execute("两个检索")

    tool_messages = [m for m in llm.calls[2]["messages"] if m.get("role") == "tool"]
    first = json.loads(tool_messages[0]["content"])
    second = json.loads(tool_messages[1]["content"])
    assert first["evidence"][0]["evidence_id"] == "E1"
    assert second["evidence"][0]["evidence_id"] == "E2"
    assert second["sources"][0]["evidence_ids"] == ["E2"]


def test_duplicate_chunk_across_tool_calls_is_dropped(monkeypatch):
    """同一 chunk 被两次检索命中时只保留首条证据，第二次不再分配 E 编号。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    evidence = {
        "status": "success",
        "type": "knowledge_evidence",
        "evidence": [{"evidence_id": "E1", "chunk_id": "same", "content": "x"}],
        "sources": [{"source_id": "s1", "evidence_ids": ["E1"]}],
        "evidence_count": 1,
        "continue_tool_loop": True,
    }
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "q1"}),
            _tool_call_response("search_knowledge", {"query": "q2"}),
            _text_response("见 [E1]"),
        ]
    )
    runtime, _ = _make_runtime(llm, FakeWM({"search_knowledge": [evidence, dict(evidence)]}))

    runtime.execute("同一 chunk 两次")

    tool_messages = [m for m in llm.calls[2]["messages"] if m.get("role") == "tool"]
    first = json.loads(tool_messages[0]["content"])
    second = json.loads(tool_messages[1]["content"])
    assert [item["evidence_id"] for item in first["evidence"]] == ["E1"]
    assert second["evidence"] == []
    assert second["sources"] == []
    assert second["evidence_count"] == 0
    assert "未新增证据" in second["message"]


def _evidence_result() -> dict:
    """构造带一条可引用证据的检索工具结果。"""
    return {
        "status": "success",
        "type": "knowledge_evidence",
        "query": "TP53",
        "evidence": [
            {"evidence_id": "E1", "chunk_id": "c1", "title": "A paper", "content": "TP53 证据"}
        ],
        "sources": [{"source_id": "s1", "evidence_ids": ["E1"]}],
        "evidence_count": 1,
        "continue_tool_loop": True,
    }


def test_invalid_citation_is_retried_once_then_accepted(monkeypatch):
    """首次回答引用不存在的编号，重写一次后通过校验。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "TP53"}),
            _text_response("见 [E9]"),
            _text_response("重写后见 [E1]"),
        ]
    )
    runtime, _ = _make_runtime(llm, FakeWM({"search_knowledge": _evidence_result()}))

    result = runtime.execute("TP53")

    assert len(llm.calls) == 3
    assert "tools" not in llm.calls[2]
    assert result["status"] == "success"
    assert result["message"] == "重写后见 [E1]"
    assert result["outcome"] == "supported_answer"


def test_invalid_citation_after_retry_reports_and_keeps_evidence(monkeypatch):
    """两次引用校验都失败时明确报错，并把已检索证据带回结果。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "TP53"}),
            _text_response("没有引用"),
            _text_response("仍然没有引用"),
        ]
    )
    runtime, _ = _make_runtime(llm, FakeWM({"search_knowledge": _evidence_result()}))

    result = runtime.execute("TP53")

    assert result["status"] == "error"
    assert result["outcome"] == "citation_invalid"
    assert "未通过引用校验" in result["message"]
    assert "[E1]" in result["message"]
    assert result["results"]["knowledge"]["outcome"] == "citation_invalid"
    assert result["results"]["knowledge"]["evidence"][0]["evidence_id"] == "E1"


def test_successful_empty_search_reports_insufficient_evidence(monkeypatch):
    """检索成功但零证据时给固定话术，不依赖模型自行“猜没有”。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "冷门问题"}),
            _text_response("我猜没有证据"),
        ]
    )
    empty = {
        "status": "success",
        "type": "knowledge_evidence",
        "query": "冷门问题",
        "evidence": [],
        "sources": [],
        "evidence_count": 0,
        "retrieval_status": "no_results",
        "message": "当前知识库没有检索到相关证据。",
        "outcome": "insufficient_evidence",
        "continue_tool_loop": True,
    }
    runtime, _ = _make_runtime(llm, FakeWM({"search_knowledge": empty}))

    result = runtime.execute("冷门问题")

    assert result["status"] == "success"
    assert result["outcome"] == "insufficient_evidence"
    assert "当前知识库没有检索到相关证据" in result["message"]
    assert "检索范围：冷门问题" in result["message"]
    assert len(llm.calls) == 2  # 不做引用重试


def test_failed_then_empty_search_stays_unavailable(monkeypatch):
    """一次故障加一次成功空结果仍报不可用：不能把未覆盖的检索说成没有证据。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "q1"}),
            _tool_call_response("search_knowledge", {"query": "q2"}),
            _text_response("没有证据"),
        ]
    )
    failed = {
        "status": "error",
        "type": "knowledge_evidence",
        "query": "q1",
        "outcome": "knowledge_unavailable",
        "message": "知识检索服务暂时不可用，请稍后重试。",
        "continue_tool_loop": True,
    }
    empty = {
        "status": "success",
        "type": "knowledge_evidence",
        "query": "q2",
        "evidence": [],
        "sources": [],
        "evidence_count": 0,
        "outcome": "insufficient_evidence",
        "continue_tool_loop": True,
    }
    runtime, _ = _make_runtime(llm, FakeWM({"search_knowledge": [failed, empty]}))

    result = runtime.execute("两次检索")

    assert result["status"] == "error"
    assert result["outcome"] == "knowledge_unavailable"
    assert result["message"] == "知识检索服务暂时不可用，请稍后重试。"


def test_service_failure_reports_unavailable_not_missing_evidence(monkeypatch):
    """检索服务不可用时用固定不可用话术，覆盖模型可能编造的“没有证据”。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")
    llm = FakeLLM(
        [
            _tool_call_response("search_knowledge", {"query": "TP53"}),
            _text_response("知识库里没有证据"),
        ]
    )
    failed = {
        "status": "error",
        "type": "knowledge_evidence",
        "query": "TP53",
        "outcome": "knowledge_unavailable",
        "message": "知识检索服务暂时不可用，请稍后重试。",
        "retrieval_error_reason": "service_failure",
        "continue_tool_loop": True,
    }
    runtime, _ = _make_runtime(llm, FakeWM({"search_knowledge": failed}))

    result = runtime.execute("TP53")

    assert result["status"] == "error"
    assert result["outcome"] == "knowledge_unavailable"
    assert result["message"] == "知识检索服务暂时不可用，请稍后重试。"


def test_run_analysis_merges_regex_params_from_user_input():
    llm = FakeLLM([_tool_call_response("run_analysis", {"analysis_type": "single_cell"})])
    wm = FakeWM({"run_analysis": {"status": "success", "message": "ok"}})
    runtime, wm = _make_runtime(llm, wm)

    runtime.execute("分析 data/pbmc.csv 的单细胞聚类")

    params = wm.calls[0][1]["params"]
    assert params.get("input_files") == ["data/pbmc.csv"]


def test_router_hint_injected_into_system_prompt():
    """传入 router 时，分类与最佳实践出现在 system prompt。"""
    from src.control.agent_runtime import AgentRuntime

    class FakeRouter:
        def hint(self, user_input):
            return {
                "modality": "analysis",
                "skill": "differential_expression",
                "best_practices": "DEG 分析建议先做质控",
            }

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=FakeRouter())
    messages = rt._build_messages("差异表达", {}, hint=rt.router.hint("差异表达"))
    assert "任务路由提示" in messages[0]["content"]
    assert "analysis" in messages[0]["content"]
    assert "DEG 分析建议先做质控" in messages[0]["content"]


def test_router_none_keeps_system_prompt_unchanged():
    """router=None 时 system prompt 与原状一致（无路由段）。"""
    from src.control.agent_runtime import AgentRuntime
    from src.control.tools import SYSTEM_PROMPT

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object())
    messages = rt._build_messages("差异表达", {})
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert rt.router is None


def test_router_hint_failure_does_not_block():
    """hint 抛异常时 execute 不被阻断（降级为无提示）。"""
    from src.control.agent_runtime import AgentRuntime

    class BrokenRouter:
        def hint(self, user_input):
            raise RuntimeError("kg down")

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=BrokenRouter())
    assert rt._route_hint("任意输入") is None


def test_build_messages_general_hint_not_injected():
    """hint modality=general 时 system prompt 保持原样（不追加路由段）。"""
    from src.control.agent_runtime import AgentRuntime
    from src.control.tools import SYSTEM_PROMPT

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object())
    messages = rt._build_messages(
        "差异表达", {}, hint={"modality": "general", "skill": None, "best_practices": None}
    )
    assert messages[0]["content"] == SYSTEM_PROMPT


class _ScriptedRouter:
    """同时实现 hint() 与 read_analysis_plan() 的路由器替身。"""

    def __init__(self, modality="analysis", output=None, raise_on_plan=False, best_practices=None):
        self.modality = modality
        self.output = output
        self.raise_on_plan = raise_on_plan
        self.best_practices = best_practices
        self.plan_calls: list[str] = []
        self.plan_best_practices: list[str | None] = []

    def hint(self, user_input):
        return {
            "modality": self.modality,
            "skill": None,
            "best_practices": self.best_practices,
        }

    def read_analysis_plan(self, user_input, *, best_practices=None):
        self.plan_calls.append(user_input)
        self.plan_best_practices.append(best_practices)
        if self.raise_on_plan:
            raise RuntimeError("classifier down")
        return self.output


def test_skill_plan_none_without_router():
    """router=None 时不产方案也不报错（离线路径保持原状）。"""
    from src.control.agent_runtime import AgentRuntime

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object())
    assert rt._skill_plan("对这份数据做差异表达分析", "differential_expression") is None


def test_skill_plan_returns_output_for_analysis_input():
    """分析类输入只调用方案读取，不触发生命周期路由。"""
    from src.control.agent_runtime import AgentRuntime

    plan = {"analysis_type": "differential_expression", "plan": ["质控", "DESeq2"]}
    router = _ScriptedRouter(output=plan, best_practices="优先 DESeq2")
    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=router)

    assert rt._skill_plan("对这份数据做差异表达分析", "differential_expression") == plan
    assert router.plan_calls == ["对这份数据做差异表达分析"]
    assert router.plan_best_practices == ["优先 DESeq2"]


def test_skill_plan_skips_non_analysis_modality():
    """非分析类输入不读取分析方案。"""
    from src.control.agent_runtime import AgentRuntime

    router = _ScriptedRouter(modality="fetch", output={"query": "x", "count": 0})
    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=router)

    assert rt._skill_plan("帮我下载 GSE123456 数据集", "differential_expression") is None
    assert router.plan_calls == []


def test_skill_plan_degrades_on_failure_and_mismatch():
    """方案读取异常或 analysis_type 不一致时一律降级为 None。"""
    from src.control.agent_runtime import AgentRuntime

    class ErrorRouter(_ScriptedRouter):
        def read_analysis_plan(self, user_input, *, best_practices=None):
            self.plan_calls.append(user_input)
            raise RuntimeError("boom")

    class MismatchRouter(_ScriptedRouter):
        def read_analysis_plan(self, user_input, *, best_practices=None):
            self.plan_calls.append(user_input)
            return {"analysis_type": "single_cell", "plan": ["聚类"]}

    routers = [
        _ScriptedRouter(raise_on_plan=True),
        ErrorRouter(),
        MismatchRouter(),
        _ScriptedRouter(output={"analysis_type": "differential_expression"}),
    ]
    for router in routers:
        rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=router)
        assert rt._skill_plan("对这份数据做差异表达分析", "differential_expression") is None


def test_skill_plan_failure_log_describes_plan_read(caplog):
    """方案读取异常应使用准确诊断名称，避免误报为技能路由失败。"""
    from src.control.agent_runtime import AgentRuntime

    caplog.set_level(logging.WARNING, logger="src.control.agent_runtime")
    router = _ScriptedRouter(raise_on_plan=True)
    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=router)

    assert rt._skill_plan("对这份数据做差异表达分析", "differential_expression") is None
    assert "analysis plan read failed, continue without plan: classifier down" in caplog.text
    assert "skill route failed" not in caplog.text


def _analysis_runtime(router, wm_results=None):
    """构造一个只回放一次 run_analysis tool-call 的 runtime。"""
    from src.control.agent_runtime import AgentRuntime

    llm = FakeLLM(
        [_tool_call_response("run_analysis", {"analysis_type": "differential_expression"})]
    )
    wm = FakeWM({"run_analysis": (wm_results or {"status": "needs_input", "message": "缺输入"})})
    return AgentRuntime(llm_client=llm, model="m", workflow_manager=wm, router=router), wm


def test_run_analysis_injects_skill_plan_without_polluting_context():
    """生产接线：方案挂进 params；用户的 HITL context 不得被写入 script_approved。"""
    plan = {
        "analysis_type": "differential_expression",
        "required_inputs": ["表达矩阵"],
        "plan": ["质控", "DESeq2"],
    }
    router = _ScriptedRouter(output=plan)
    runtime, wm = _analysis_runtime(router)

    context = {"downloaded_assets": [{"access_path": "counts.csv"}]}
    result = runtime.execute("对这份数据做差异表达分析", context=context)

    assert result["status"] == "needs_input"
    assert wm.calls[0][1]["params"]["skill_plan"] == plan
    assert "script_approved" not in context


def test_run_analysis_route_failure_keeps_params_and_dispatch():
    """方案读取炸了也不影响分析下发：params 无 skill_plan，终态照常返回。"""
    router = _ScriptedRouter(raise_on_plan=True)
    runtime, wm = _analysis_runtime(router)

    result = runtime.execute("对这份数据做差异表达分析", context={})

    assert wm.calls[0][0] == "run_analysis"
    assert "skill_plan" not in wm.calls[0][1]["params"]
    assert result["status"] == "needs_input"


def test_build_messages_no_history_cap():
    """废除 MAX_HISTORY=10：15 条历史应全部进入 messages。"""
    from src.control.agent_runtime import AgentRuntime

    wm = object.__new__(type("W", (), {}))  # 不会被用到
    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=wm)
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(15)
    ]
    out = rt._build_messages("最新问题", {"history": history})
    assert out[0]["role"] == "system"
    assert out[-1] == {"role": "user", "content": "最新问题"}
    # 15 条历史 + 1 条 system + 1 条当前输入
    assert len(out) == 17
    assert out[1]["content"] == "m0"
    assert out[-2]["content"] == "m14"


def test_build_messages_injects_checkpoint_after_system():
    """context.summary 存在时，在 system 之后、历史之前注入 checkpoint。"""
    from src.control.agent_runtime import AgentRuntime

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object())
    history = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"}]
    out = rt._build_messages("新问题", {"history": history, "summary": "## 目标\n做差异"})
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert "<conversation-checkpoint>" in out[1]["content"]
    assert "做差异" in out[1]["content"]
    assert out[2] == {"role": "user", "content": "旧问题"}
    assert out[-1] == {"role": "user", "content": "新问题"}


def test_build_messages_without_summary_has_no_checkpoint():
    from src.control.agent_runtime import AgentRuntime

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object())
    out = rt._build_messages("q", {"history": [{"role": "user", "content": "a"}]})
    assert len(out) == 3  # system + history + 当前输入
    assert not any("<conversation-checkpoint>" in str(m.get("content")) for m in out)


def test_qc_plan_and_hint_do_not_call_shared_router():
    """受控 QC 不走最佳实践查询或带 teardown 的技能生命周期。"""
    from src.control.agent_runtime import AgentRuntime

    class ForbiddenRouter:
        def hint(self, user_input):
            raise AssertionError("QC must not query shared KG")

        def route(self, user_input, context):
            raise AssertionError("QC must not write skill KG")

    rt = AgentRuntime(None, "m", object(), ForbiddenRouter())
    assert rt._route_hint("单细胞 RNA 分析")["best_practices"] is None
    plan = rt._skill_plan("单细胞 RNA 分析", "single_cell")
    assert plan["analysis_type"] == "single_cell"
    assert "不执行过滤、聚类或注释" in str(plan["plan"])


def test_qc_results_and_artifacts_do_not_enter_model_history():
    """持久化 results 不是授权模型读取的摘要。"""
    rt, _ = _make_runtime(None)
    out = rt._build_messages(
        "继续",
        {
            "history": [
                {
                    "role": "assistant",
                    "content": "QC 等待审阅",
                    "results": {
                        "qc": {"private": "CELL_SECRET"},
                        "qc_artifacts": {"x": "PRIVATE_PATH"},
                    },
                }
            ]
        },
    )
    assert "CELL_SECRET" not in str(out) and "PRIVATE_PATH" not in str(out)
    assert out[1] == {"role": "assistant", "content": "QC 等待审阅"}
