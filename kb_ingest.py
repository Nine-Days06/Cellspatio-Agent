"""Resume LightRAG ingestion for documents already enqueued in the knowledge base.

Documents left in `pending` keep their full text in `lightrag_doc_full`, so this
only drives the extraction/merge pipeline; nothing is re-imported.

Usage:
    python kb_ingest.py              # run until no pending work is left
    python kb_ingest.py --status     # print status counts and exit
    python kb_ingest.py --requeue stuck   # put processing/parsing/analyzing back to pending
    python kb_ingest.py --requeue failed  # retry documents marked failed
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.config  # noqa: F401 - loads .env before LightRAG reads MAX_ASYNC etc.
from src.knowledge.async_runtime import runtime
from src.knowledge.lightrag_client import LightRAGClient

WORKSPACE = "knowledge_base"
REPORT_SECONDS = 60
STUCK_STATUSES = ("processing", "parsing", "analyzing")


async def _connect():
    """Open a connection to the PG backend of the knowledge base."""
    import asyncpg

    return await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DATABASE", "rag"),
    )


async def status() -> dict[str, int]:
    """Count documents per status for the workspace."""
    conn = await _connect()
    try:
        rows = await conn.fetch(
            "select status, count(*) n from lightrag_doc_status where workspace=$1 group by status",
            WORKSPACE,
        )
    finally:
        await conn.close()
    return {r["status"]: r["n"] for r in rows}


async def requeue(statuses: tuple[str, ...]) -> int:
    """Put documents back to pending; full text stays in lightrag_doc_full."""
    conn = await _connect()
    try:
        res = await conn.execute(
            "update lightrag_doc_status set status='pending', error_msg=null"
            " where workspace=$1 and status = any($2::text[])",
            WORKSPACE,
            list(statuses),
        )
    finally:
        await conn.close()
    return int(res.split()[-1])


async def pump(client: LightRAGClient) -> None:
    """Drive the LightRAG pipeline; one call drains until the queue is idle."""
    while True:
        try:
            await client._rag.apipeline_process_enqueue_documents()
        except Exception as exc:  # noqa: BLE001 - long run must survive a failed batch
            print(
                f"{time.strftime('%H:%M:%S')} batch failed: {type(exc).__name__}: {exc}", flush=True
            )
        await asyncio.sleep(5)


async def run_locked(client: LightRAGClient) -> None:
    """Single-instance run: hold a PG advisory lock, requeue stuck docs, then pump.

    The lock connection lives until the run ends, so a second launch exits instead
    of fighting the first one over the same documents.
    """
    lock = await _connect()
    try:
        got = await lock.fetchval("select pg_try_advisory_lock(hashtext('kb_ingest'))")
        if not got:
            print("another kb_ingest instance is running, exit", flush=True)
            return
        print("requeued stuck", await requeue(STUCK_STATUSES), flush=True)
        await run(client)
    finally:
        await lock.close()


async def run(client: LightRAGClient) -> None:
    """Print progress every minute until no pending document is left."""
    started = time.monotonic()
    base = await status()
    print(f"start {base}", flush=True)
    task = asyncio.create_task(pump(client))
    try:
        while True:
            await asyncio.sleep(REPORT_SECONDS)
            counts = await status()
            done = counts.get("processed", 0) - base.get("processed", 0)
            hours = (time.monotonic() - started) / 3600
            rate = done / hours if hours else 0
            print(
                f"{time.strftime('%H:%M:%S')} {counts} total+{done} rate={rate:.0f}/h", flush=True
            )
            if counts.get("pending", 0) == 0:
                print("done: no pending documents", flush=True)
                return
    finally:
        task.cancel()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--requeue", choices=("stuck", "failed"))
    args = parser.parse_args()
    if args.status:
        print(runtime.run(status()))
        runtime.shutdown()
        return 0
    if args.requeue:
        statuses = STUCK_STATUSES if args.requeue == "stuck" else ("failed",)
        print("requeued", runtime.run(requeue(statuses)))
        print(runtime.run(status()))
        runtime.shutdown()
        return 0
    client = LightRAGClient(working_dir=WORKSPACE)
    client._initialize_rag()
    try:
        runtime.run(run_locked(client))
    finally:
        client.close()
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
