"""Test main.py LLM wiring and HITL switch"""


def test_main_wires_llm_components(monkeypatch):
    from src.main import CellSpatioAgent

    fake_client = object()
    monkeypatch.setattr(
        "src.main.get_current_llm", lambda: (fake_client, "test-model")
    )
    # 若 LightRAG 构造过重：monkeypatch LightRAGClient.__init__ 为 no-op
    monkeypatch.setattr(
        "src.main.LightRAGClient.__init__", lambda self, *a, **k: None
    )
    agent = CellSpatioAgent(
        config={"knowledge_dir": "tmp_kb_wiring", "data_dir": "tmp_data_wiring"}
    )
    assert agent.intent_parser.llm_client is fake_client
    assert agent.intent_parser.model == "test-model"
    assert agent.code_repairer.llm_client is fake_client
    assert agent.result_explainer.llm_client is fake_client
    assert agent.result_explainer.model == "test-model"
    assert agent.r_script_generator.llm_client is fake_client
    assert agent.workflow_manager.require_script_confirmation is True


def test_agent_runtime_wiring(monkeypatch):
    """验证 CellSpatioAgent 注入 AgentRuntime 并委托 execute_workflow"""
    from src.control.agent_runtime import AgentRuntime
    from src.main import CellSpatioAgent

    fake_client = object()
    monkeypatch.setattr(
        "src.main.get_current_llm", lambda: (fake_client, "test-model")
    )
    monkeypatch.setattr(
        "src.main.LightRAGClient.__init__", lambda self, *a, **k: None
    )
    agent = CellSpatioAgent(
        config={"knowledge_dir": "tmp_kb_wiring", "data_dir": "tmp_data_wiring"}
    )

    # 验证 AgentRuntime 实例化并持有正确依赖
    assert hasattr(agent, "agent_runtime"), "CellSpatioAgent 应持有 agent_runtime 属性"
    assert isinstance(agent.agent_runtime, AgentRuntime), "agent_runtime 应为 AgentRuntime 实例"
    assert agent.agent_runtime.llm_client is fake_client
    assert agent.agent_runtime.model == "test-model"
    assert agent.agent_runtime.workflow_manager is agent.workflow_manager

    # 验证 execute_workflow 委托给 agent_runtime.execute
    call_log = []

    def spy_execute(user_input, context=None):
        call_log.append((user_input, context))
        return {"status": "success", "type": "general_response", "message": "ok"}

    agent.agent_runtime.execute = spy_execute

    result = agent.execute_workflow("测试输入", context={"history": [{"role": "user", "content": "prev"}]})

    assert len(call_log) == 1
    assert call_log[0][0] == "测试输入"
    assert call_log[0][1]["history"] == [{"role": "user", "content": "prev"}]
    assert call_log[0][1]["last_user_input"] == "测试输入"
    assert result["message"] == "ok"