"""意图词表：意图解析器与任务分类器共用的唯一来源。

只收录 WorkflowManager 真正能执行的分析子类型；通路富集、独立绘图等能力未实现，
放进词表只会让解析结果进入"不支持的分析类型"错误分支。
"""

from __future__ import annotations

ANALYSIS_TYPES: dict[str, str] = {
    "differential_expression": "差异表达分析、差异基因、DEG、fold change",
    "single_cell": "单细胞分析、scRNA、Seurat、降维、聚类、UMAP、细胞类型注释、细胞亚群",
    "spatial": "空间转录组、空间组学、Visium、spatial、时空、空间位置、组织原位",
}

ANALYSIS_KEYWORDS: dict[str, list[str]] = {
    "differential_expression": ["差异表达", "差异基因", "DEG", "fold change"],
    "single_cell": [
        "单细胞",
        "scRNA",
        "scrna",
        "Seurat",
        "seurat",
        "UMAP",
        "细胞聚类",
        "细胞注释",
    ],
    "spatial": ["空间转录", "空间组", "Visium", "visium", "spatial", "时空组"],
}

KNOWLEDGE_KEYWORDS: list[str] = [
    "是什么",
    "有什么",
    "有哪些",
    "哪些",
    "怎么",
    "如何",
    "作用",
    "功能",
    "关系",
    "解释",
    "机制",
    "途径",
    "通路",
    "含义",
    "意义",
    "定义",
    "介绍",
    "简介",
    "概述",
    "特点",
    "特征",
    "特性",
    "分类",
    "类型",
    "种类",
]

DATASET_KEYWORDS: list[str] = ["下载", "获取数据", "数据集", "找数据", "数据下载"]

SOURCE_KEYWORDS: dict[str, list[str]] = {
    "geo": ["geo", "gse", "数据集", "表达谱"],
    "kegg": ["kegg", "pathway", "通路"],
    "uniprot": ["uniprot", "蛋白", "protein"],
    "reactome": ["reactome", "通路详情", "pathway"],
    "hpa": ["hpa", "human protein atlas", "蛋白图谱", "亚细胞定位"],
}

FETCH_KEYWORDS: list[str] = list(
    dict.fromkeys(
        ["获取"] + DATASET_KEYWORDS + [kw for kws in SOURCE_KEYWORDS.values() for kw in kws]
    )
)
