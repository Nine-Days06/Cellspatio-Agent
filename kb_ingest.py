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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.config  # noqa: F401 - loads .env before LightRAG reads MAX_ASYNC etc.
from src.knowledge.async_runtime import runtime
from src.knowledge.knowledge_admin import KnowledgeAdmin, idle
from src.knowledge.lightrag_client import LightRAGClient

WORKSPACE = "knowledge_base"
STUCK_STATUSES = ("processing", "parsing", "analyzing")
admin = KnowledgeAdmin(WORKSPACE)
_idle = idle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--requeue", choices=("stuck", "failed"))
    args = parser.parse_args()
    if args.status:
        print(runtime.run(admin.status()))
        runtime.shutdown()
        return 0
    if args.requeue:
        statuses = STUCK_STATUSES if args.requeue == "stuck" else ("failed",)
        print("requeued", runtime.run(admin.requeue(statuses)))
        print(runtime.run(admin.status()))
        runtime.shutdown()
        return 0
    client = LightRAGClient(working_dir=WORKSPACE)
    client._initialize_rag()
    try:
        runtime.run(admin.run_locked(client))
    finally:
        client.close()
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
