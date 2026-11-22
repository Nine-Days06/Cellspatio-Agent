"""KGMemory 查询结果归一：LightRAG 的 aquery 默认返回整段字符串。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.control import kg_memory as kg_memory_module
from src.control.kg_memory import KGMemory


def _memory(answer: object, monkeypatch: pytest.MonkeyPatch) -> KGMemory:
    """构造不初始化后端的 KGMemory，aquery 直接返回给定 answer。"""

    async def aquery(*_args: object, **_kwargs: object) -> object:
        return answer

    memory = object.__new__(KGMemory)
    memory._rag = SimpleNamespace(aquery=aquery)
    memory._ensure_initialized = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(kg_memory_module.async_runtime, "run", asyncio.run)
    return memory


def test_query_entities_keeps_string_answer_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _memory("TP53 与 DNA 修复相关", monkeypatch)

    assert memory.query_entities("TP53") == ["TP53 与 DNA 修复相关"]


def test_query_relations_keeps_string_answer_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _memory("TP53 -[regulates]-> MDM2", monkeypatch)

    assert memory.query_relations("TP53") == [{"text": "TP53 -[regulates]-> MDM2"}]


def test_query_entities_preserves_list_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _memory(["a", "b"], monkeypatch)

    assert memory.query_entities("x") == ["a", "b"]
