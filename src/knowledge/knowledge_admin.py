"""知识库运维：状态查询、重排与 LightRAG pipeline 驱动。

直接访问 LightRAG 的 PG 状态表是该后端目前唯一公开的运维方式；集中在此，
避免脚本各自拼 SQL。连接与时钟可注入，便于测试。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

STUCK_STATUSES = ("processing", "parsing", "analyzing")


async def default_connect() -> Any:
    """按环境变量连接知识库 PG。"""
    import asyncpg

    return await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DATABASE", "rag"),
    )


def idle(counts: dict[str, int]) -> bool:
    """没有排队任务，且没有停留在处理中状态。"""
    return counts.get("pending", 0) == 0 and not any(
        counts.get(status, 0) for status in STUCK_STATUSES
    )


class KnowledgeAdmin:
    """知识库运维命令的单一实现。"""

    def __init__(
        self,
        workspace: str = "knowledge_base",
        *,
        connect: Callable[[], Awaitable[Any]] = default_connect,
        report_seconds: float = 60,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.workspace = workspace
        self._connect = connect
        self.report_seconds = report_seconds
        self._sleep = sleep
        self._clock = clock

    async def status(self) -> dict[str, int]:
        """按状态统计当前 workspace 的文档数。"""
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                "select status, count(*) n from lightrag_doc_status"
                " where workspace=$1 group by status",
                self.workspace,
            )
        finally:
            await conn.close()
        return {row["status"]: row["n"] for row in rows}

    async def requeue(self, statuses: tuple[str, ...]) -> int:
        """把指定状态改回 pending；文档全文仍在 lightrag_doc_full。"""
        conn = await self._connect()
        try:
            result = await conn.execute(
                "update lightrag_doc_status set status='pending', error_msg=null"
                " where workspace=$1 and status = any($2::text[])",
                self.workspace,
                list(statuses),
            )
        finally:
            await conn.close()
        return int(result.split()[-1])

    async def pump(self, client: Any) -> None:
        """持续驱动 LightRAG 队列，单批失败不终止长任务。"""
        while True:
            try:
                await client._rag.apipeline_process_enqueue_documents()
            except Exception as exc:  # noqa: BLE001 - 长任务必须跨过单批失败
                print(
                    f"{time.strftime('%H:%M:%S')} batch failed: {type(exc).__name__}: {exc}",
                    flush=True,
                )
            await self._sleep(5)

    async def run_locked(self, client: Any) -> None:
        """单实例运行：持有 PG advisory lock，重排卡住文档后驱动 pipeline。"""
        lock = await self._connect()
        try:
            got = await lock.fetchval("select pg_try_advisory_lock(hashtext('kb_ingest'))")
            if not got:
                print("another kb_ingest instance is running, exit", flush=True)
                return
            print("requeued stuck", await self.requeue(STUCK_STATUSES), flush=True)
            await self.run(client)
        finally:
            await lock.close()

    async def run(self, client: Any) -> None:
        """每分钟打印进度，直到没有 pending 或处理中的文档。"""
        started = self._clock()
        base = await self.status()
        print(f"start {base}", flush=True)
        task = asyncio.create_task(self.pump(client))
        try:
            while True:
                await self._sleep(self.report_seconds)
                counts = await self.status()
                done = counts.get("processed", 0) - base.get("processed", 0)
                hours = (self._clock() - started) / 3600
                rate = done / hours if hours else 0
                print(
                    f"{time.strftime('%H:%M:%S')} {counts} total+{done} rate={rate:.0f}/h",
                    flush=True,
                )
                if idle(counts):
                    print("done: no pending documents", flush=True)
                    return
        finally:
            task.cancel()
