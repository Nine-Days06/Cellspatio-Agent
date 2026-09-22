"""方法学知识库（第二 LightRAG 实例，与主事实库隔离）"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MethodsKb:
    """方法卡片摄入与「只取上下文」检索，供 R 代码生成前注入"""

    def __init__(self, client: Any, cards_dir: str | Path | None = None):
        """
        Args:
            client: LightRAGClient（或兼容的 query_context/insert_documents 对象）
            cards_dir: 方法卡片目录（build_from_cards 默认路径）
        """
        self.client = client
        self.cards_dir = (
            Path(cards_dir) if cards_dir else Path("knowledge_base_methods_cards")
        )

    def query_context(self, question: str, mode: str = "hybrid") -> str:
        """检索方法学上下文；失败返回空串（不阻断分析主流程）"""
        try:
            return self.client.query_context(question, mode=mode)
        except Exception as e:  # 方法库是增强层，必须降级
            logger.warning("MethodsKb query failed: %s", e)
            return ""

    def build_from_cards(self, cards_dir: str | Path | None = None) -> int:
        """读取目录下全部 .md 卡片并批量插入，返回插入数"""
        directory = Path(cards_dir) if cards_dir else self.cards_dir
        if not directory.exists():
            logger.warning("方法卡片目录不存在: %s", directory)
            return 0
        texts = [
            p.read_text(encoding="utf-8")
            for p in sorted(directory.glob("*.md"))
            if p.name.upper() != "README.MD"
        ]
        if not texts:
            return 0
        return self.client.insert_documents(texts)
