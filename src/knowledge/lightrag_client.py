import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LightRAGClient:
    """LightRAG 客户端封装"""

    def __init__(self, working_dir: str, config: dict[str, Any] | None = None):
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(exist_ok=True)
        self.config = config or {}

        # 延迟导入 LightRAG，避免立即依赖
        self._rag = None

    def _initialize_rag(self):
        """使用真实 LLM（云端 DeepSeek 等）与 embedding（本地 Ollama bge-m3）初始化"""
        if self._rag is None:
            try:
                from lightrag import LightRAG

                ollama_url = self.config.get("ollama_url") or os.getenv("OLLAMA_HOST")
                if ollama_url:
                    os.environ["OLLAMA_HOST"] = ollama_url.rstrip("/")
                os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")

                from src.knowledge.llm_factory import (
                    build_embedding_func,
                    build_llm_func,
                )

                llm_func, llm_model = build_llm_func(self.config.get("provider"))
                embedding_func = build_embedding_func(self.config.get("embedding_func"))
                self._validate_embedding_model(embedding_func)

                self._rag = LightRAG(
                    working_dir=str(self.working_dir),
                    llm_model_func=llm_func,
                    llm_model_name=llm_model,
                    embedding_func=embedding_func,
                    enable_llm_cache=True,
                    llm_model_max_async=2,  # 并发限制，受 API 限流影响
                )
                # LightRAG 1.5.7+ 需要显式初始化存储
                asyncio.run(self._rag.initialize_storages())
                logger.info("LightRAG initialized: llm=%s embedding=%s",
                            llm_model, getattr(embedding_func, "model_name", "custom"))
            except ImportError:
                logger.error("LightRAG not installed. Install with: pip install lightrag-hku")
                raise

    def insert_document(self, document: str, metadata: dict[str, Any] | None = None):
        """增量插入单篇文档"""
        self._initialize_rag()
        if self._rag:
            self._rag.insert(document)
            logger.info(f"Document inserted, length: {len(document)}")

    def insert_documents(self, documents: list[str]) -> int:
        """批量插入文档（批量构建与导入统一走这里），返回插入数量"""
        self._initialize_rag()
        if not documents or self._rag is None:
            return 0
        self._rag.insert(documents)
        logger.info(f"Batch inserted {len(documents)} documents")
        return len(documents)

    def query(self, question: str, mode: str = "hybrid") -> str:
        """查询知识库"""
        self._initialize_rag()
        if self._rag:
            from lightrag import QueryParam
            return self._rag.query(question, param=QueryParam(mode=mode))
        return "LightRAG 未初始化"

    def query_context(self, question: str, mode: str = "hybrid") -> str:
        """只返回检索到的上下文片段（不生成回答），供注入 codegen prompt"""
        self._initialize_rag()
        if not self._rag:
            return ""
        from lightrag import QueryParam
        return self._rag.query(
            question,
            param=QueryParam(mode=mode, only_need_context=True),
        )

    def insert_knowledge_graph(self, kg_data: dict[str, Any]):
        """插入知识图谱数据"""
        self._initialize_rag()
        if self._rag:
            # LightRAG 支持自定义知识图谱插入
            self._rag.insert_custom_kg(kg_data)

    def get_statistics(self) -> dict[str, Any]:
        """获取知识库统计信息"""
        return {
            "working_dir": str(self.working_dir),
            "initialized": self._rag is not None,
        }

    def _validate_embedding_model(self, embedding_func):
        """embedding 锁定校验：一旦建库写入模型标识，后续必须一致"""
        model_name = getattr(embedding_func, "model_name", None) or "custom"
        lock_file = self.working_dir / "EMBEDDING_MODEL.json"
        if lock_file.exists():
            saved = json.loads(lock_file.read_text(encoding="utf-8")).get("model")
            if saved != model_name:
                raise RuntimeError(
                    f"embedding 模型已锁定为 {saved}，不能改为 {model_name}；如需更换需清空知识库重建"
                )
        else:
            lock_file.write_text(
                json.dumps({"model": model_name}, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("embedding 模型已锁定: %s", model_name)