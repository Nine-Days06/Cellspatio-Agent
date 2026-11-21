"""知识库运维适配层：状态、重排与空闲判定。"""

from __future__ import annotations

import asyncio
from typing import Any

from src.knowledge.knowledge_admin import KnowledgeAdmin, idle


class FakeConn:
    """记录 SQL 的只读连接桩。"""

    def __init__(self, rows: list[dict[str, Any]], result: str = "UPDATE 2") -> None:
        self.rows = rows
        self.result = result
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        return self.rows

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return self.result

    async def fetchval(self, sql: str) -> bool:
        self.calls.append((sql, ()))
        return True

    async def close(self) -> None:
        self.closed = True


def run(coro: Any) -> Any:
    """在当前线程跑完协程。"""
    return asyncio.run(coro)


def test_idle_requires_no_queue_or_inflight() -> None:
    """pending 与 processing 任一非零都不算空闲。"""
    assert idle({"processed": 5})
    assert not idle({"pending": 1})
    assert not idle({"processing": 1})


def test_status_scopes_workspace_and_closes() -> None:
    """状态查询绑定 workspace，并关闭连接。"""
    conn = FakeConn([{"status": "pending", "n": 3}])
    admin = KnowledgeAdmin("ws", connect=lambda: _ready(conn))
    assert run(admin.status()) == {"pending": 3}
    assert conn.calls[0][1] == ("ws",)
    assert conn.closed


def test_requeue_returns_updated_rows() -> None:
    """重排只改目标状态，行数来自 PG 命令标签。"""
    conn = FakeConn([], result="UPDATE 4")
    admin = KnowledgeAdmin("ws", connect=lambda: _ready(conn))
    assert run(admin.requeue(("failed",))) == 4
    assert conn.calls[0][1] == ("ws", ["failed"])


async def _ready(value: Any) -> Any:
    return value
