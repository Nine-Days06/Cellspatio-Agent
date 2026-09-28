"""AgentRuntime: tool-calling dispatch, HITL short-circuit, no-tool direct, legacy fallback."""
from __future__ import annotations

import json
from types import SimpleNamespace


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
    llm = FakeLLM([_tool_call_response("run_analysis", {"analysis_type": "differential_expression", "question": "做差异"})])
    wm = FakeWM({"run_analysis": {"status": "success", "analysis_type": "differential_expression", "message": "ok", "results": {}}})
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute("对这份数据做差异表达分析", context={"downloaded_assets": [{"access_path": "a.csv"}]})

    assert result["status"] == "success"
    assert wm.calls[0][0] == "run_analysis"
    assert wm.calls[0][1]["analysis_type"] == "differential_expression"
    sent = llm.calls[0]["messages"]
    assert sent[-1]["role"] == "user"
    assert "tools" in llm.calls[0]


def test_hitl_needs_confirmation_short_circuits_without_second_llm_call():
    llm = FakeLLM([_tool_call_response("search_datasets", {"query": "GSE123456"})])
    wm = FakeWM({"search_datasets": {"status": "needs_confirmation", "type": "fetch_data", "candidates": [], "message": "选一个"}})
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


def test_query_knowledge_dispatch():
    llm = FakeLLM([_tool_call_response("query_knowledge", {"query": "TP53 在癌症中的作用"})])
    wm = FakeWM({"query_knowledge": {"status": "success", "type": "knowledge_response", "response": "抑癌", "references": []}})
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute("TP53 在癌症中的作用是什么？")

    assert wm.calls == [("query_knowledge", {"query": "TP53 在癌症中的作用"})]
    assert result["type"] == "knowledge_response"


def test_query_memory_dispatch():
    """query_memory 工具调用分发到 workflow_manager，返回记忆问答终态。"""
    llm = FakeLLM([_tool_call_response("query_memory", {"query": "上次差异表达用的什么参数"})])
    wm = FakeWM({"query_memory": {
        "status": "success", "type": "memory_response",
        "response": "run-abc123 使用 DESeq2，FDR=0.05",
    }})
    runtime, wm = _make_runtime(llm, wm)

    result = runtime.execute("上次那个差异表达分析用的什么参数？")

    assert wm.calls == [("query_memory", {"query": "上次差异表达用的什么参数"})]
    assert result["type"] == "memory_response"
    assert result["status"] == "success"


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
            return {"modality": "analysis", "skill": "differential_expression",
                    "best_practices": "DEG 分析建议先做质控"}

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(),
                      router=FakeRouter())
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

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(),
                      router=BrokenRouter())
    assert rt._route_hint("任意输入") is None


def test_build_messages_general_hint_not_injected():
    """hint modality=general 时 system prompt 保持原样（不追加路由段）。"""
    from src.control.agent_runtime import AgentRuntime
    from src.control.tools import SYSTEM_PROMPT

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object())
    messages = rt._build_messages("差异表达", {},
                                  hint={"modality": "general", "skill": None,
                                        "best_practices": None})
    assert messages[0]["content"] == SYSTEM_PROMPT


class _ScriptedRouter:
    """同时实现 hint() 与 route() 的路由器替身；route 记录调用参数。"""

    def __init__(self, modality="analysis", output=None, raise_on_route=False):
        self.modality = modality
        self.output = output
        self.raise_on_route = raise_on_route
        self.route_calls: list[tuple[str, dict]] = []

    def hint(self, user_input):
        return {"modality": self.modality, "skill": None, "best_practices": None}

    def route(self, user_input, context=None):
        self.route_calls.append((user_input, dict(context or {})))
        if self.raise_on_route:
            raise RuntimeError("classifier down")
        return {"status": "success", "modality": self.modality, "skill": "stub",
                "output": self.output}


def test_skill_plan_none_without_router():
    """router=None 时不产方案也不报错（离线路径保持原状）。"""
    from src.control.agent_runtime import AgentRuntime

    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object())
    assert rt._skill_plan("对这份数据做差异表达分析", "differential_expression") is None


def test_skill_plan_returns_output_for_analysis_input():
    """分析类输入：route() 被调且返回静态方案；HITL 门以新建 dict 显式放行。"""
    from src.control.agent_runtime import AgentRuntime

    plan = {"analysis_type": "differential_expression", "plan": ["质控", "DESeq2"]}
    router = _ScriptedRouter(output=plan)
    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=router)

    assert rt._skill_plan("对这份数据做差异表达分析", "differential_expression") == plan
    assert router.route_calls == [("对这份数据做差异表达分析", {"script_approved": True})]


def test_skill_plan_skips_non_analysis_modality():
    """非分析类输入不调 route()：fetch/knowledge 技能真实打网络，不得重复执行。"""
    from src.control.agent_runtime import AgentRuntime

    router = _ScriptedRouter(modality="fetch", output={"query": "x", "count": 0})
    rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=router)

    assert rt._skill_plan("帮我下载 GSE123456 数据集", "differential_expression") is None
    assert router.route_calls == []


def test_skill_plan_degrades_on_failure_and_mismatch():
    """route 抛异常 / status 非 success / analysis_type 不一致 → 一律降级为 None。"""
    from src.control.agent_runtime import AgentRuntime

    class ErrorRouter(_ScriptedRouter):
        def route(self, user_input, context=None):
            self.route_calls.append((user_input, dict(context or {})))
            return {"status": "error", "modality": "analysis", "message": "boom"}

    class MismatchRouter(_ScriptedRouter):
        def route(self, user_input, context=None):
            self.route_calls.append((user_input, dict(context or {})))
            return {"status": "success", "modality": "analysis", "skill": "single_cell",
                    "output": {"analysis_type": "single_cell", "plan": ["聚类"]}}

    routers = [
        _ScriptedRouter(raise_on_route=True),
        ErrorRouter(),
        MismatchRouter(),
        _ScriptedRouter(output={"analysis_type": "differential_expression"}),
    ]
    for router in routers:
        rt = AgentRuntime(llm_client=None, model="m", workflow_manager=object(), router=router)
        assert rt._skill_plan("对这份数据做差异表达分析", "differential_expression") is None


def _analysis_runtime(router, wm_results=None):
    """构造一个只回放一次 run_analysis tool-call 的 runtime。"""
    from src.control.agent_runtime import AgentRuntime

    llm = FakeLLM([
        _tool_call_response("run_analysis", {"analysis_type": "differential_expression"})
    ])
    wm = FakeWM({"run_analysis": (wm_results or {"status": "needs_input", "message": "缺输入"})})
    return AgentRuntime(llm_client=llm, model="m", workflow_manager=wm, router=router), wm


def test_run_analysis_injects_skill_plan_without_polluting_context():
    """生产接线：方案挂进 params；用户的 HITL context 不得被写入 script_approved。"""
    plan = {"analysis_type": "differential_expression",
            "required_inputs": ["表达矩阵"], "plan": ["质控", "DESeq2"]}
    router = _ScriptedRouter(output=plan)
    runtime, wm = _analysis_runtime(router)

    context = {"downloaded_assets": [{"access_path": "counts.csv"}]}
    result = runtime.execute("对这份数据做差异表达分析", context=context)

    assert result["status"] == "needs_input"
    assert wm.calls[0][1]["params"]["skill_plan"] == plan
    assert "script_approved" not in context


def test_run_analysis_route_failure_keeps_params_and_dispatch():
    """route() 炸了也不影响分析下发：params 无 skill_plan，终态照常返回。"""
    router = _ScriptedRouter(raise_on_route=True)
    runtime, wm = _analysis_runtime(router)

    result = runtime.execute("对这份数据做差异表达分析", context={})

    assert wm.calls[0][0] == "run_analysis"
    assert "skill_plan" not in wm.calls[0][1]["params"]
    assert result["status"] == "needs_input"