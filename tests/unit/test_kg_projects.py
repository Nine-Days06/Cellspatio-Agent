"""文件型 KG：不依赖 PG，按项目隔离 working_dir。"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from lightrag.utils import EmbeddingFunc

from src.control.kg_memory import KGMemory
from src.control.kg_projects import ProjectKGMemory
from src.knowledge.lightrag_client import build_storage_kwargs
from src.ui.session_store import SessionStore


async def _llm(prompt: str, **kwargs) -> str:
    if "keyword" in prompt.lower():
        return '{"high_level_keywords": ["test"], "low_level_keywords": ["run"]}'
    if "entity" in prompt.lower() or "relation" in prompt.lower():
        return '{"entities": [], "relationships": []}'
    return "测试记忆：使用了 DESeq2。"


async def _embed(texts: list[str], **kwargs) -> np.ndarray:
    return np.array([[0.1] * 768 for _ in texts], dtype=np.float32)


def _embedding() -> EmbeddingFunc:
    return EmbeddingFunc(embedding_dim=768, max_token_size=8192, func=_embed)


def _execution(run_id: str, session_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        intent=SimpleNamespace(original_input=f"test {run_id}"),
        steps=[],
        outputs=[],
        meta={"context": {"session_id": session_id}},
    )


def test_file_backend_ingests_without_postgres(tmp_path, monkeypatch):
    for key in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DATABASE",
    ):
        monkeypatch.delenv(key, raising=False)

    assert build_storage_kwargs("file")["kv_storage"] == "JsonKVStorage"
    assert "POSTGRES_HOST" not in os.environ

    memory = KGMemory(
        tmp_path / "kg",
        storage_backend="file",
        llm_model_func=_llm,
        embedding_func=_embedding(),
    )
    try:
        memory.ingest(_execution("run-1", None))
        assert isinstance(memory.query_entities("run-1"), list)
        assert (tmp_path / "kg").is_dir()
    finally:
        memory.close()


def test_project_memory_uses_isolated_working_dirs(tmp_path):
    store = SessionStore()
    liver = store.create_project("肝癌")
    brain = store.create_project("脑")
    liver_session = store.create_session("肝脏会话", project_id=liver)
    brain_session = store.create_session("脑会话", project_id=brain)
    memory = ProjectKGMemory(tmp_path / "kg", llm_model_func=_llm, embedding_func=_embedding())
    try:
        memory.ingest(_execution("run-liver", liver_session))
        memory.ingest(_execution("run-brain", brain_session))

        assert (tmp_path / "kg" / liver).is_dir()
        assert (tmp_path / "kg" / brain).is_dir()
        assert set(memory._memories) == {liver, brain}
        assert "DESeq2" in memory.query("上次用了什么", {"session_id": liver_session})
    finally:
        memory.close()


def test_project_memory_is_singleton_under_concurrency(tmp_path):
    store = SessionStore()
    pid = store.create_project("并发项目")
    sid = store.create_session("会话", project_id=pid)
    memory = ProjectKGMemory(tmp_path / "kg", llm_model_func=_llm, embedding_func=_embedding())
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            instances = list(pool.map(lambda _: memory.memory_for(sid), range(8)))
        assert len({id(item) for item in instances}) == 1
    finally:
        memory.close()


def test_project_memory_ingest_failure_does_not_raise(tmp_path, monkeypatch):
    memory = ProjectKGMemory(tmp_path / "kg")
    monkeypatch.setattr(memory, "memory_for", Mock(side_effect=RuntimeError("boom")))
    memory.ingest(_execution("run-1", None))


def _build_agent(monkeypatch, tmp_path, local_kg: bool):
    import src.main as main

    monkeypatch.setattr(main, "get_current_llm", lambda: (object(), "test-model"))
    monkeypatch.setattr("src.main.LightRAGClient.__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(main, "is_local_mode", lambda: True)
    monkeypatch.setattr(main, "env_flag", lambda key: key == "CELLSPATIO_LOCAL_KG" and local_kg)
    monkeypatch.setattr(main, "build_llm_func", lambda: (_llm, "test-model"))
    monkeypatch.setattr(main, "build_embedding_func", _embedding)
    return main.CellSpatioAgent(
        config={"knowledge_dir": str(tmp_path / "kb"), "data_dir": str(tmp_path / "data")}
    )


def test_local_kg_is_off_by_default(tmp_path, monkeypatch):
    agent = _build_agent(monkeypatch, tmp_path, local_kg=False)
    try:
        assert agent.local_kg is None
        assert agent.kg_memory is None
        assert agent.workflow_recorder.kg_memory is None
        assert agent.workflow_manager.kg_query is None
    finally:
        agent.close()


def test_local_kg_enabled_wires_project_memory(tmp_path, monkeypatch):
    agent = _build_agent(monkeypatch, tmp_path, local_kg=True)
    try:
        assert isinstance(agent.local_kg, ProjectKGMemory)
        assert agent.workflow_recorder.kg_memory is agent.local_kg
        assert agent.workflow_manager.kg_query is agent.local_kg
        assert agent.modal_router.kg_memory is None
    finally:
        agent.close()
