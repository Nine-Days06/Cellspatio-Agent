"""Test main.py LLM wiring and HITL switch"""
import pytest


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