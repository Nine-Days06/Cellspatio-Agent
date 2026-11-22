"""任务分类器：意图文本 → 模态/技能映射。"""

from __future__ import annotations

from typing import Any, ClassVar

from src.control.intent_vocabulary import (
    ANALYSIS_KEYWORDS,
    FETCH_KEYWORDS,
    KNOWLEDGE_KEYWORDS,
)

# 分类器比解析器更宽松：单个"聚类"/"时空"也足以判定模态
_ANALYSIS_EXTRA: dict[str, list[str]] = {
    "single_cell": ["聚类"],
    "spatial": ["时空"],
}


class TaskClassifier:
    """基于关键词 + LLM 兜底的任务分类器。"""

    # 高优先级：问题词优先匹配 knowledge
    QUESTION_KEYWORDS: ClassVar[list[str]] = ["为什么"] + KNOWLEDGE_KEYWORDS

    MODALITY_KEYWORDS: ClassVar[dict[str, dict[str, list[str]]]] = {
        "analysis": {
            analysis_type: keywords + _ANALYSIS_EXTRA.get(analysis_type, [])
            for analysis_type, keywords in ANALYSIS_KEYWORDS.items()
        },
        "fetch": {"search_datasets": FETCH_KEYWORDS},
        "knowledge": {"query_knowledge": KNOWLEDGE_KEYWORDS},
    }

    def __init__(self):
        self._compile_patterns()

    def _compile_patterns(self):
        self._patterns = {}
        # 先添加问题词（高优先级）
        for kw in self.QUESTION_KEYWORDS:
            self._patterns[kw] = ("knowledge", "query_knowledge")
        # 再添加普通关键词
        for modality, sub_skills in self.MODALITY_KEYWORDS.items():
            for sub_skill, keywords in sub_skills.items():
                for kw in keywords:
                    if kw not in self._patterns:  # 不覆盖已有的高优先级词
                        self._patterns[kw] = (modality, sub_skill)

    def classify(self, text: str) -> dict[str, Any]:
        text_lower = text.lower()
        # 先检查问题词（高优先级，不看长度）
        for kw in self.QUESTION_KEYWORDS:
            if kw in text_lower:
                modality, skill = self._patterns[kw]
                return {"modality": modality, "skill": skill}
        # 再检查普通关键词（按长度排序）
        for kw in sorted(self._patterns.keys(), key=len, reverse=True):
            if kw not in self.QUESTION_KEYWORDS and kw in text_lower:
                modality, skill = self._patterns[kw]
                return {"modality": modality, "skill": skill}
        # 兜底：LLM 分类（Phase 3 后续接入）
        return {"modality": "general", "skill": None}
