# 数据获取层与 LightRAG 配置设计文档

## 1. 背景与目标

现有知识层只完成了 LightRAG 封装骨架，存在三个缺口：

1. **LightRAG 未接入真实模型**：`lightrag_client.py` 中 `llm_model_func` 与 `embedding_func` 默认均为 `None`，GraphRAG 无法真正执行实体抽取、关系构建与向量检索。
2. **数据源单一**：知识库仅规划 PubMed 文献，缺少公共数据库数据；且 `build_from_pubmed` 与 pubmed-etl 存在功能重复。
3. **数据流未落地**：没有统一的公共数据获取层，分析数据与知识数据边界不清。

本文档目标是打通「公共数据库获取 → 双通道数据流」与「LightRAG 真实配置」，完成试点并定义扩展机制。

## 2. 现状与问题

| 现有模块 | 现状 | 问题 |
|---|---|---|
| `src/knowledge/lightrag_client.py` | 封装 `LightRAG(...)`，仅传 `working_dir` | LLM/embedding 均未配置，无法工作 |
| `src/knowledge/knowledge_builder.py` | `build_from_pubmed` 为占位符 | 与 pubmed-etl 管线重复 |
| `src/knowledge/knowledge_importer.py` | 从 pubmed-etl 产物（json/csv/sqlite）批量导入 | 已可用，作为文献唯一通道 |
| `src/knowledge/api_gateway.py` | 简化版 PubMed/KEGG API 调用 | 将被 fetchers 取代：`query_pubmed` 弃用，`query_kegg` 迁移至 KEGG fetcher |
| `src/data/` | 目录存在，暂无数据获取实现 | 需扩展 fetcher |

**关键决策：文献来源仅保留 pubmed-etl 产物，不再直接调用 PubMed API。**

## 3. 总体设计（双通道数据流）

```
公共数据库（GEO / KEGG / UniProt）
        │
   ┌────▼───────────────────────────────┐
   │        数据获取层 src/data/         │
   │  BaseFetcher 统一下载与转换         │
   │  GEO / KEGG / UniProt 三个实现     │
   │  storage.py 落盘与缓存              │
   └────┬───────────────┬───────────────┘
        │               │
  分析流│               │知识流
        ▼               ▼
┌──────────────┐  ┌───────────────────┐
│ 分析数据       │  │ 知识文本           │
│ data/raw/geo/ │  │ 通路/蛋白/元数据   │
│ Series Matrix │  │ Markdown 文本       │
└──────┬───────┘  └───────┬───────────┘
       │                   │ insert
       ▼                   ▼
┌──────────────┐  ┌───────────────────┐
│ RExecutor    │  │ LightRAGClient     │
│ R 分析脚本    │  │ GraphRAG 问答       │
└──────────────┘  └───────────────────┘
```

- **分析流**：GEO 表达矩阵等落到本地文件，交给 R 分析管线。
- **知识流**：KEGG 通路、UniProt 蛋白、GSE 元数据转为文本，进 LightRAG。
- **文献知识**：由 pubmed-etl 产物经 `KnowledgeImporter` 进入同一知识库。

## 4. 数据获取层

新增 `src/data/fetchers/` 与 `src/data/storage.py`、`src/data/registry.py`。

### 4.1 统一 fetcher 接口

```python
# src/data/fetchers/base.py
class AssetMeta:
    asset_id: str     # 唯一标识（如 GSE12345、hsa00010、P04637）
    title: str
    source: str       # geo / kegg / uniprot
    is_analysis: bool # 分析流 or 知识流

class BaseFetcher:
    def search(self, query: str) -> list[AssetMeta]: ...  # 检索候选
    def confirm(self, asset_id: str) -> AssetInfo: ...     # 详情供用户确认
    def download(self, asset_id: str) -> Path: ...         # 分析流：落 data/raw
    def ingest(self, asset_id: str) -> str: ...            # 知识流：返回文本
```

扩展新数据源 = 新增一个 fetcher 类并注册，不修改既有流程。

### 4.2 GEO Fetcher（分析流）

- `search`：NCBI E-utilities `esearch`（`db=gds`），复用已有 `NCBI_API_KEY` / `NCBI_EMAIL`。
- `download`：获取 Series Matrix 文件（`.txt.gz`），来源 `https://ftp.ncbi.nlm.nih.gov/geo/series/`，落 `data/raw/geo/<GSE>/`。
- `ingest`：GSE 元数据（标题、摘要、平台、样本数）转文本，供「找数据集」类问答。

### 4.3 KEGG Fetcher（知识流）

- `search`：KEGG REST API `list` / `find` 检索通路。
- `ingest`：`rest.kegg.jp/get/<pathway_id>` 通路条目转为 Markdown 文本。
- 需配置免费 API key（`KEGG_API_KEY`）提升频率限额。

### 4.4 UniProt Fetcher（知识流）

- `search`：`rest.uniprot.org/uniprotkb/search?query=...`。
- `ingest`：蛋白条目（功能、基因、互作、通路注释）转 Markdown 文本。

### 4.5 存储与缓存

- `storage.py`：管理 `data/raw/<source>/...` 资产落盘，按 `asset_id` 记录下载状态，避免重复下载。
- `registry.py`：fetcher 注册表，按 source 分发。

## 5. 知识库构建（初建批量 + 按需增量）

采用混合模式：**初始批量建库 + 日常按需增量扩展**。LightRAG 为增量式图构建，新数据可持续插入同一知识库，相同实体（如 `TP53`）跨源自动融合。

### 5.1 文献来源（仅 pubmed-etl）

`KnowledgeImporter` 作为文献批量导入的**唯一通道**，从 pubmed-etl 导出目录批量导入：

- `import_from_directory(pubmed_etl_output_dir)` 覆盖 json/csv/sqlite 产物。
- `build_from_pubmed` 原占位符**弃用**，不再实现直接 PubMed API 调用。
- `import_from_*` 现为逐条 `insert_document`，改为批量 `insert(list)` 提升效率。

### 5.2 批量构建改造

`KnowledgeBuilder.build_initial_knowledge_base(config)` 作为总入口，按 `sources` 调度：

| 现有骨架 | 改造点 |
|---|---|
| `build_from_kegg(pathway_ids)` | 接 KEGG fetcher，通路全集批量 ingest 文本 |
| `build_from_files` / `build_from_articles` | 改为批量 `insert(list)`，分批 + 进度日志 + 断点续建 |
| —（新增） | `build_from_uniprot`：接 UniProt fetcher 批量构建 |
| —（新增） | `build_from_geo_metadata`：GSE 元数据批量入库 |

### 5.3 增量更新

- 新文献：pubmed-etl 重跑后重新 import（LightRAG 同名实体去重）。
- 新公共数据：fetcher 按需拉取后 `insert_document` 入库。
- 不重建全图。

## 6. LightRAG 配置

### 6.1 模型选型

| 角色 | 方案 | 依据 |
|---|---|---|
| Embedding | **本地 Ollama `bge-m3`** | 中英混合知识库多语言强；本地化可控；8G 显存有余量 |
| EXTRACT 实体抽取 | **云端 DeepSeek API** | 官方建议抽取模型 30B 级起步，本地 8G 卡无法胜任 |
| KEYWORDS / QUERY | 云端 DeepSeek（flash） | 快且省 |

### 6.2 角色级 LLM 配置

LightRAG 1.5.7 支持 EXTRACT / QUERY / KEYWORDS 角色分离（构造参数 `role_llm_configs`）：

```python
from lightrag import LightRAG
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.llm.ollama import ollama_embed
from lightrag.utils import EmbeddingFunc

async def llm_func(model, messages, **kwargs):
    return await openai_complete_if_cache(
        api_key=config["api_key"], model=model, messages=messages,
        base_url=config["base_url"], **kwargs)

async def embed_texts(texts):
    return await ollama_embed(texts, host="http://localhost:11434", model="bge-m3")

rag = LightRAG(
    working_dir=working_dir,
    llm_model_func=llm_func,
    llm_model_name=config["model"],             # DeepSeek，用于 EXTRACT 等
    embedding_func=EmbeddingFunc(
        embedding_dim=1024,
        max_token_size=8192,
        func=embed_texts,                       # 本地 bge-m3
    ),
)
```

- 抽取配置：temperature=0 + `response_format: json_object`（复用 `src/config.py` 现有配置）。
- QUERY / KEYWORDS 如需独立模型，通过 `role_llm_configs` 指定（如 QUERY 用速度更快的模型）。

### 6.3 embedding 锁定约束

**一旦用 bge-m3 建库，不可更换 embedding 模型**（向量维度/分布固定，更换需全量重建）。该项作为首次建库前的硬校验。

## 7. 工作流集成

| 组件 | 改动 |
|---|---|
| `intent_parser` | 新增意图 `fetch_data`（如「获取糖尿病相关的表达数据」） |
| `workflow_manager` | 新增流程：检索 → 列候选 → 用户确认 → 分流（分析流/RExecutor 或知识流/LightRAG） |
| UI | 候选列表选择交互 + 下载进度展示 |

## 8. 配置项（.env）

| 变量 | 用途 | 必填 |
|---|---|---|
| `NCBI_API_KEY` / `NCBI_EMAIL` | GEO 检索（已有） | GEO 生效时 |
| `KEGG_API_KEY` | KEGG 频率限额 | 可选，推荐 |
| `OLLAMA_URL` | 本地 Ollama 地址（默认 `http://localhost:11434`） | 否 |
| `EMBEDDING_MODEL` | 默认 `bge-m3` | 否 |
| `DEEPSEEK_*` | EXTRACT/QUERY 云端 LLM（已有） | 是 |

`.env.example` 在现有基础上**新增** `KEGG_API_KEY` / `OLLAMA_URL` / `EMBEDDING_MODEL` 三项，无需删除现有项（`NCBI_*` 供 GEO 复用）。

## 9. 错误处理与缓存

- fetcher 单点失败仅记日志，不影响主流程；API 限流指数退避重试。
- 下载缓存按 `asset_id` 落盘，重复请求直接命中，避免重复拉取。
- LLM 抽取失败批量场景支持断点续建。
- 知识流 insert 失败记录告警，不中断其余批次。

## 10. 测试策略

| 级别 | 内容 |
|---|---|
| 单元 | fetcher 用 mock HTTP；文本转换函数；缓存命中逻辑 |
| 集成 | KEGG 单条通路 ingest、UniProt 单条蛋白 ingest、小 GSE 元数据 |
| 验证 | `python -m pytest tests/ -v`，遵循现有测试目录结构 |

## 11. 试点里程碑

1. **M1**：三个 fetcher 可检索 + 下载，storage 落盘与缓存生效。
2. **M2**：知识流打通（KEGG/UniProt → LightRAG 问答），LightRAG 配置落地（本地 bge-m3 + 云端 DeepSeek）。
3. **M3**：分析流打通（GEO → R 差异分析）。
4. **M4**：初始批量建库（pubmed-etl + KEGG + UniProt）+ 工作流/UI 集成 + 增量更新。

## 12. 非目标（范围外）

- 不做 GEO 之外的大规模全库镜像。
- 不实现直接 PubMed API 检索（仅 pubmed-etl 产物）。
- 不引入除 LightRAG 外的图数据库（存储后端仍为 LightRAG 本地 JSON 默认）。
- 单细胞库、代谢组库等列为未来扩展，仅需新增 fetcher 即可接入。