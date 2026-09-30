# 项目间接口规范

## 概述

本文档定义 pubmed-etl（独立文献批量下载与清洗工具）与 cellspatio-agent（单细胞与时空组学分析智能体）之间的接口规范。

## 数据流向

```
pubmed-etl --step export → data/output/articles_<ts>.csv → data/import/ → import_cli（增量） → LightRAG 知识库
```

一键脚本：`python sync_pubmed.py`（导出 + 复制 + 增量导入）。

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

## 导入规范（cellspatio-agent 侧）

**导入目录：** `data/import/`

**导入台账（ledger）：** `data/import/import_ledger.json`

```json
{
  "version": 1,
  "files": {
    "articles_20260921_130000.csv": {
      "imported_at": "2026-09-30T10:00:00",
      "count": 12,
      "sha256": "…64 位十六进制…"
    }
  }
}
```

- **键**：CSV 文件名（时间戳批次）。ETL 导出侧已用 `exported_pmids.txt` 保证每批次只含全新文章且文件名唯一，主项目侧不再另建 PMID 台账，避免出现两个相互竞争的去重事实来源。
- **行为**：默认只导入台账未记录的文件；重复执行为空操作；删除/清空台账文件即触发一次有意的全量重导。
- **边界处理**：台账损坏或不可读 → 记 warning 并按空台账处理（宁可重复导入也不崩溃）；台账条目对应的 CSV 已删除 → 条目保留、不影响流程；CSV 导入后被修改 → 不自动重导（记录的 `sha256` 仅作审计，LightRAG 按内容哈希/文件名去重，重复提交不会更新旧文档），需要重导时删除该条目或加 `--reimport-all`。

**触发方式：**
- 手动（增量）：`python -c "from src.knowledge.import_cli import main; raise SystemExit(main(['--dir', 'data/import']))"`，加 `--reimport-all` 则忽略台账全量重导
- 一键：`python sync_pubmed.py`（导出 + 复制 + 只导入本次新 CSV）

> `python -m src.knowledge.import_cli` 因模块缺少 `__main__` 入口而静默无输出（已知限制），请使用上面的 `python -c` 写法。

**去重机制（两层，各司其职）：**
1. 导出侧：`exported_pmids.txt` 保证 CSV 批次只含新 PMID（唯一事实来源）；
2. 导入侧：`import_ledger.json` 保证同一 CSV 批次只被读取/提交一次（批次粒度）。

**计数口径：** 导入结果的 `new_count`（即脚本输出的 `imported`）= 台账未记录文件中的文章数，即首次提交给 LightRAG 的数量；`total_count` = 实际提交的文章总数（扫描数）。两者都不等于知识库实际新增的文档数——LightRAG 1.5.7 在 pipeline 内部按 `content_hash` 与文件名静默去重，且不通过 SDK 返回逐文档去重信号，该数字在导入层不可观测。

## 一键脚本

```
python sync_pubmed.py                 # 导出 + 增量导入本次新 CSV
python sync_pubmed.py --no-import     # 只导出
python sync_pubmed.py --reimport-all  # 忽略台账，全量重导 data/import/ 下所有文件
```

核心函数 `sync_and_import(etl_dir, import_dir, do_import, reimport_all)`：
1. `subprocess` 调用 `pubmed-etl/main.py --step export`
2. 复制最新 `data/output/articles_<ts>.csv` 到主项目 `data/import/`
3. 经 `import_cli.import_from_directory(..., files=[该文件名])` 只导入这一个文件（台账去重）；`reimport_all=True` 时改为全量扫描目录

**返回键：** `exported`（0/1）、`imported`（新批次文章数，见上方计数口径）、`failed`（0=成功，1=失败）、`csv`（复制后的文件路径，无新数据为 `None`）。

## 错误处理

- 无新文献可导：导出侧返回 `None`，脚本输出 `exported: 0`，不报错
- 文件已被台账记录：跳过不读，`skipped_files` 计数，不报错（重跑即空操作）
- 导入失败：`import_cli` 返回 `{"success": False, "error": ...}`，脚本 `failed: 1`

## 版本兼容性

**当前版本：** v2.1（v2.0 + 导入侧台账增量契约：新增 `new_count` / `skipped_files` / `failed_files` 返回键与 `--reimport-all` 标志，原有键与原有 CLI 参数保持不变）

**字段扩展：** 新增字段可选，不影响导入；必需字段：pmid、title、abstract。
