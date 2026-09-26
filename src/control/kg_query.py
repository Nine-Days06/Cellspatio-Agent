"""KGQuery：基于 LightRAG 的自然语言查询接口。"""
from __future__ import annotations

from pathlib import Path

from lightrag.lightrag import QueryParam

from src.control.kg_memory import KGMemory


class KGQuery:
    """自然语言查询接口，封装 KGMemory 提供高层查询能力。"""

    def __init__(self, working_dir: Path | str, kg_memory: KGMemory | None = None):
        """
        初始化 KGQuery。
        
        Args:
            working_dir: 工作目录
            kg_memory: 可选的 KGMemory 实例。如果不提供，将创建新实例。
                      测试时建议传入已有的 KGMemory 实例以复用 LightRAG 共享存储。
        """
        self._kg_memory = kg_memory or KGMemory(working_dir)

    def query(self, question: str, mode: str = "hybrid") -> str:
        """自然语言提问，返回生成式回答。"""
        self._kg_memory._ensure_initialized()
        loop = self._kg_memory._get_loop()
        param = QueryParam(mode=mode)
        result = loop.run_until_complete(self._kg_memory._rag.aquery(question, param=param))
        return str(result) if result else "未找到相关信息。"

    def query_entities(self, query: str) -> list[str]:
        """实体检索：返回相关实体文本片段。"""
        return self._kg_memory.query_entities(query)

    def query_relations(self, entity: str) -> list[dict]:
        """关系查询：返回实体关系。"""
        return self._kg_memory.query_relations(entity)

    def ingest_workflow(self, execution) -> None:
        """摄入工作流记录。"""
        self._kg_memory.ingest(execution)