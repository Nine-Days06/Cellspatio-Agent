import logging
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
        """初始化 LightRAG 实例"""
        if self._rag is None:
            try:
                from lightrag import LightRAG
                
                # 配置 LLM 和嵌入函数（需要用户提供）
                llm_func = self.config.get('llm_func')
                embedding_func = self.config.get('embedding_func')
                
                self._rag = LightRAG(
                    working_dir=str(self.working_dir),
                    llm_model_func=llm_func,
                    embedding_func=embedding_func,
                )
                logger.info("LightRAG initialized successfully")
            except ImportError:
                logger.error("LightRAG not installed. Install with: pip install lightrag-hku")
                raise
    
    def insert_document(self, document: str, metadata: dict[str, Any] | None = None):
        """插入文档到知识库"""
        self._initialize_rag()
        if self._rag:
            self._rag.insert(document)
            logger.info(f"Document inserted, length: {len(document)}")
    
    def query(self, question: str, mode: str = "hybrid") -> str:
        """查询知识库"""
        self._initialize_rag()
        if self._rag:
            return self._rag.query(question, param={"mode": mode})
        return "LightRAG 未初始化"
    
    def insert_knowledge_graph(self, kg_data: dict[str, Any]):
        """插入知识图谱数据"""
        self._initialize_rag()
        if self._rag:
            # LightRAG 支持自定义知识图谱插入
            self._rag.insert_custom_kg(kg_data)
    
    def get_statistics(self) -> dict[str, Any]:
        """获取知识库统计信息"""
        # 简化实现
        return {
            "working_dir": str(self.working_dir),
            "initialized": self._rag is not None,
        }
