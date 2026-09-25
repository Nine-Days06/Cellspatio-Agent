"""任务分类器：意图文本 → 模态/技能映射。"""
from __future__ import annotations

from typing import Any, ClassVar


class TaskClassifier:
    """基于关键词 + LLM 兜底的任务分类器。"""
    
    # 高优先级：问题词优先匹配 knowledge
    QUESTION_KEYWORDS: ClassVar[list[str]] = [
        "为什么", "是什么", "有什么", "有哪些", "哪些", "怎么", "如何",
        "作用", "功能", "关系", "解释", "机制", "途径", "含义", "解释",
        "特点", "特征", "特性", "分类", "类型", "种类"
    ]
    
    MODALITY_KEYWORDS: ClassVar[dict[str, dict[str, list[str]]]] = {
        "analysis": {
            "differential_expression": ["差异表达", "差异基因", "DEG", "fold change"],
            "single_cell": ["单细胞", "scRNA", "Seurat", "UMAP", "聚类", "细胞注释"],
            "spatial": ["空间转录", "空间组", "Visium", "spatial", "时空"],
        },
        "fetch": {
            "search_datasets": ["下载", "获取", "GEO", "GSE", "数据集", "表达谱",
                                "KEGG", "pathway", "通路",
                                "UniProt", "蛋白", "protein"],
        },
        "knowledge": {
            "query_knowledge": ["是什么", "有什么", "有哪些", "哪些", "怎么", "如何",
                                "作用", "功能", "关系", "解释", "机制", "途径", "含义", "解释",
                                "特点", "特征", "特性", "分类", "类型", "种类"],
        }
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