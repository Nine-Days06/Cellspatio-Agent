# 项目间接口规范

## 概述

本文档定义 pubmed-etl（人类多组学文献处理项目）与 multiomics-agent（人类多组学分析智能体）之间的接口规范。

## 数据流向

```
pubmed-etl → 导出文件 → multiomics-agent → 导入 → LightRAG 知识库
```

## 导出格式规范

### JSON 格式

**文件名：** `approved_articles_YYYYMMDD_HHMMSS.json`

**结构：**
```json
[
  {
    "pmid": "string",
    "title": "string",
    "abstract": "string",
    "keywords": ["string"],
    "mesh_terms": ["string"],
    "authors": ["string"],
    "year": "integer",
    "journal": "string",
    "human_review": "Y/N",
    "llm_verdict": "relevant/irrelevant/marginal",
    "llm_relevance_score": "float (0.0-1.0)",
    "export_timestamp": "ISO 8601"
  }
]
```

### CSV 格式

**文件名：** `approved_articles_YYYYMMDD_HHMMSS.csv`

**字段：**
- pmid, title, abstract, keywords, mesh_terms, authors, year, journal, human_review, llm_verdict, llm_relevance_score, export_timestamp

**注意：**
- 数组字段（keywords, mesh_terms, authors）使用分号分隔
- 编码：UTF-8

### SQLite 格式

**文件名：** `approved_articles_YYYYMMDD_HHMMSS.db`

**表结构：**
```sql
CREATE TABLE articles (
    pmid TEXT PRIMARY KEY,
    title TEXT,
    abstract TEXT,
    keywords TEXT,  -- 分号分隔
    mesh_terms TEXT,  -- 分号分隔
    authors TEXT,  -- 分号分隔
    year INTEGER,
    journal TEXT,
    human_review TEXT,
    llm_verdict TEXT,
    llm_relevance_score REAL,
    export_timestamp TEXT
);
```

## 导入规范

### 导入目录

**默认目录：** `data/import/`

**文件放置：**
- 将导出的文件复制到 `data/import/` 目录
- 支持同时存在多种格式
- 支持增量导入

### 导入触发

**手动导入：**
```python
from src.knowledge.knowledge_importer import KnowledgeImporter
importer = KnowledgeImporter(lightrag_client)
importer.import_from_directory("data/import/")
```

**自动导入（可选）：**
- 监控 `data/import/` 目录变化
- 自动导入新文件

## 错误处理

### 导入失败

**常见错误：**
1. JSON 格式错误 → 检查文件编码和格式
2. 必需字段缺失 → 检查 pmid, title, abstract 字段
3. 重复导入 → 使用增量导入或清理后重新导入

**错误日志：**
- 日志位置：`logs/import.log`
- 错误信息包含：文件名、错误类型、失败记录

### 数据一致性

**去重机制：**
- 使用 PMID 作为唯一标识
- 重复文献自动跳过（不覆盖）

## 版本兼容性

**当前版本：** v1.0

**向后兼容：**
- 新版本导出的文件兼容旧版本导入器
- 旧版本导出的文件兼容新版本导入器

**字段扩展：**
- 新增字段可选，不影响导入
- 必需字段：pmid, title, abstract
