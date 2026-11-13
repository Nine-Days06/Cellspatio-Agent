"""本地 KG 记忆：按会话所属项目懒加载独立文件型 LightRAG。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from src.control.kg_memory import KGMemory
from src.ui.session_store import SessionStore

logger = logging.getLogger(__name__)


class ProjectKGMemory:
    """每项目独立 working_dir；未归类会话落 unclassified，默认不跨项目查询。"""

    def __init__(
        self,
        root: Path | str,
        llm_model_func: Any = None,
        embedding_func: Any = None,
    ):
        """保存根目录与可选的 LLM/embedding 函数；记忆实例按需懒建。"""
        self.root = Path(root)
        self._llm_model_func = llm_model_func
        self._embedding_func = embedding_func
        self._memories: dict[str, KGMemory] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _session_id(execution: Any) -> str | None:
        """从 workflow execution 的 context 取 session_id。"""
        context = (getattr(execution, "meta", None) or {}).get("context") or {}
        return context.get("session_id")

    def _key(self, session_id: str | None) -> str:
        """会话所属项目 id；未归类会话落 unclassified。"""
        session = SessionStore().get_session(session_id) if session_id else None
        return (session or {}).get("project_id") or "unclassified"

    def memory_for(self, session_id: str | None) -> KGMemory:
        """按项目返回同一实例；首次使用时才建 LightRAG。"""
        key = self._key(session_id)
        memory = self._memories.get(key)
        if memory is not None:
            return memory
        with self._lock:
            memory = self._memories.get(key)
            if memory is None:
                memory = KGMemory(
                    working_dir=self.root / key,
                    storage_backend="file",
                    llm_model_func=self._llm_model_func,
                    embedding_func=self._embedding_func,
                )
                self._memories[key] = memory
        return memory

    def ingest(self, execution: Any) -> None:
        """WorkflowRecorder.finish_run 的 duck-typed 入口。"""
        try:
            self.memory_for(self._session_id(execution)).ingest(execution)
        except Exception as exc:  # noqa: BLE001 - 可选记忆失败不阻断分析收口
            logger.warning("local KG ingest failed: %s", exc)

    def query(self, question: str, context: dict[str, Any] | None = None) -> str:
        """WorkflowManager.query_memory_for_agent 的 duck-typed 入口。"""
        memory = self.memory_for((context or {}).get("session_id"))
        return memory.query_text(question)

    def close(self) -> None:
        """关闭全部项目记忆；幂等。"""
        for memory in self._memories.values():
            memory.close()
        self._memories.clear()
