# 项目间接口规范

## 概述

本文档定义 pubmed-etl（人类多组学文献处理项目）与 multiomics-agent（人类多组学分析智能体）之间的接口规范。

## 数据流向

```
pubmed-etl --step export → data/output/articles_*.csv → data/import/ → import_cli → LightRAG 知识库
```

一键脚本：`python sync_pubmed.py`（导出 + 复制 + 导入）。

## 导出格式规范（pubmed-etl 侧）

**文件名：** `articles_YYYYMMDD_HHMMSS.csv`（时间戳为导出时刻本地时间）

**编码：** UTF-8 含 BOM（utf-8-sig，兼容 Excel）

**字段（9 列，纯文献信息，不含 LLM 判定/人工复核元数据）：**

| 列名 | 来源 | 说明 |
|------|------|------|
| pmid | articles.pmid | 唯一标识 |
| title | articles.title | 标题 |
| abstract | articles.abstract | 摘要 |
| keywords | articles.keywords | 数组字段，`\|` 已转为 `,` |
| mesh_terms | articles.mesh_terms | 数组字段，`\|` 已转为 `,` |
| authors | articles.authors | 数组字段，`\|` 已转为 `,` |
| year | articles.pub_year | 发表年 |
| journal | articles.journal | 期刊名 |
| doi | articles.doi | DOI |

**筛选条件：** `human_review='Y'` 或（未复核且 `llm_verdict='RELEVANT'`）

**增量导出：**
- 记录文件：`pubmed-etl/data/output/exported_pmids.txt`（每行一个已导出 PMID）
- 每次导出 = 符合筛选条件的 PMID 集合 ∖ 已记录集合
- 导出成功后追写本次 PMID

## 导入规范（multiomics-agent 侧）

**导入目录：** `data/import/`

**触发方式：**
- 手动：`python -m src.knowledge.import_cli --dir data/import`
- 一键：`python sync_pubmed.py`（导出 + 复制 + 导入）

**去重机制：** 由导出侧增量保证；同一文件名不会重复导入（每批次文件名含时间戳唯一）。

## 一键脚本

```
python sync_pubmed.py                # 导出 + 导入
python sync_pubmed.py --no-import    # 只导出
```

核心函数 `sync_and_import(etl_dir, import_dir, do_import)`：
1. `subprocess` 调用 `pubmed-etl/main.py --step export`
2. 复制最新 `data/output/articles_*.csv` 到主项目 `data/import/`
3. 经 `import_cli.import_from_directory` 导入 LightRAG

## 错误处理

- 无新文献可导：导出侧返回 `None`，脚本输出 `exported: 0`，不报错
- 导入失败：`import_cli` 返回 `{"success": False, "error": ...}`，脚本 `failed: 1`

## 版本兼容性

**当前版本：** v2.0（对齐实际实现）

**字段扩展：** 新增字段可选，不影响导入；必需字段：pmid、title、abstract。
