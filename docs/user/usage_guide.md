# CellSpatio 使用指南

> **文档定位**：[README](../../README.md) 是项目概览与功能列表，本指南是**详细操作手册**，面向实验生物学家与数据分析用户，覆盖安装、配置、三大核心操作（数据获取 / 数据分析 / 知识问答）、溯源复现与常见问题。
>
> 本指南内容以代码实际行为为准。最后核对日期：2026-09-30（参考环境：Windows / Python 3.12.10 / R 4.6.1 / Streamlit 1.64.0）。

> 受控单细胞 PCA 维度记录段落更新并核对于 2026-10-28（FastAPI / React）；其余历史章节未在本次重新核对。

## 目录

1. [简介与适用人群](#1-简介与适用人群)
2. [安装](#2-安装)
3. [配置](#3-配置)
4. [启动](#4-启动)
5. [Web 界面操作](#5-web-界面操作)
6. [数据获取](#6-数据获取)
7. [数据分析](#7-数据分析)
8. [知识问答与知识库](#8-知识问答与知识库)
9. [溯源与复现](#9-溯源与复现)
10. [命令行模式](#10-命令行模式)
11. [文献子项目 pubmed-etl](#11-文献子项目-pubmed-etl)
12. [开发者评测](#12-开发者评测)
13. [常见问题 FAQ](#13-常见问题-faq)
14. [已知限制](#14-已知限制)
15. [文档维护约定](#15-文档维护约定)

## 1. 简介与适用人群

CellSpatio 是交互式人类单细胞与空间/时序组学数据分析与知识问答系统：你用自然语言描述需求，智能体检索公共数据库、生成 R 脚本（**人工确认后才执行**）、返回结果解读，并基于 LightRAG 知识库回答专业问题。

**你需要具备**：

- Windows / macOS / Linux 基本命令行操作
- Python 3.10+ 与 R 4.0+（分析功能必需）
- 一个 LLM 供应商的 API Key（智谱 / DeepSeek / OpenAI 任一）
- （可选）本地 [Ollama](https://ollama.com) + `bge-m3` 模型，用于知识库 embedding

## 2. 安装

### 2.1 一键安装（推荐，Windows）

```cmd
git clone https://github.com/Nine-Days06/cellspatio-agent.git
cd cellspatio-agent
setup.bat
```

`setup.bat` 依次执行：

1. 检查 Python（无则报错退出，需自行安装 3.10+）
2. 创建并激活 `venv` 虚拟环境（已存在则复用）
3. 升级 pip 并安装 `requirements.txt`
4. 检测 `Rscript`：找到则安装 renv 并执行 `renv::restore()`；找不到仅打印警告，**不中断**（此时分析功能不可用）
5. 创建 `knowledge_base/`、`cache/`、`metadata/` 目录
6. 若无 `.env` 则从 `.env.example` 复制，并提示填写 API Key
7. 打印启动命令

> **注意**：`requirements.txt` 含 `rpy2`，若本机缺少 R 开发工具链（如 Windows 的 Rtools）可能导致安装报错。此时可先安装 R + Rtools，或从 `requirements.txt` 移除 `rpy2` 后重装（本项目执行 R 脚本走 subprocess `Rscript`，不依赖 rpy2）。

### 2.2 手动安装

```bash
python -m venv venv
# Windows: venv\Scripts\activate    macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

# 安装 R 依赖（R 4.0+）
R -e "renv::restore()"

# 生成配置文件
copy .env.example .env        # macOS/Linux 用 cp .env.example .env
```

## 3. 配置

编辑项目根目录 `.env`，全部配置项分组如下。

### 3.1 LLM 供应商（必需）

```env
# 主项目供应商：zhipu / deepseek / openai（默认 zhipu）
AGENT_LLM_PROVIDER=zhipu
ZHIPU_API_KEY=your_key

# 其他供应商按需填写
DEEPSEEK_API_KEY=your_key
# DEEPSEEK_BASE_URL=https://api.deepseek.com
# DEEPSEEK_MODEL=deepseek-v4-flash
# ZHIPU_MODEL=glm-4-Flash-250414
OPENAI_API_KEY=your_key
# OPENAI_BASE_URL=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4

# pubmed-etl 子项目独立设置（默认 deepseek）
# ETL_LLM_PROVIDER=deepseek
```

LLM 用于：意图与工具路由、R 脚本生成与失败修复、知识库抽取与问答、结果解读。

### 3.2 知识库 embedding（推荐）

```env
# 本地 Ollama embedding（默认模型 bge-m3）
# OLLAMA_URL=http://localhost:11434
```

- 知识库首次写入时会把 embedding 模型名锁定到 `knowledge_base/EMBEDDING_MODEL.json`，之后**不可直接换模型**，需清库重建（见 [13 FAQ](#13-常见问题-faq)）。
- 未配置 Ollama 时，数据下载、分析、脚本确认等功能不受影响，仅知识库写入/问答不可用。

### 3.3 公共数据库（可选）

```env
NCBI_API_KEY=your_ncbi_api_key     # 提高 GEO/NCBI 检索与下载配额
NCBI_EMAIL=your_email@example.com
# PROXY=http://127.0.0.1:7890       # 网络代理（当前代码未引用，见「已知限制」）
```

### 3.4 按需补库（可选）

```env
# 问答上下文短于该长度时，自动补充知识入库（字符数）
LAZY_INGEST_MIN_CONTEXT=300
# 单次提问最多自动入库条数
LAZY_INGEST_MAX_ASSETS=3
```

### 3.5 脚本执行确认（可选）

```env
# 生成的 R 脚本需人工点击「确认执行」才运行（默认开启）
SCRIPT_REQUIRE_CONFIRM=1
# 设为 0 可关闭确认、直接执行（不建议）
```

## 4. 启动

### Web 界面（推荐）

```bash
streamlit run src/ui/app.py
```

启动后终端打印地址（本机参考环境为 `http://localhost:8502`，以终端输出为准），浏览器打开即可。

### 命令行模式

```bash
python -m src.main
```

详见 [第 10 章](#10-命令行模式)。**交互式确认（选数据集、批脚本）仅 Web 界面支持**，建议日常使用 Web。

## 5. Web 界面操作

### 5.1 界面布局

- **侧边栏**
  - 「启用外部 API 查询」开关：当前仅展示说明文字，暂未影响实际行为
  - 「知识库状态」：实时显示知识库工作目录与初始化状态
- **主区域**（自上而下）
  1. 对话历史（含智能体回复、结果解读、引用来源）
  2. 候选数据集选择器（数据检索确认时出现）
  3. 脚本确认卡片（生成脚本时出现）
  4. 预设示例按钮（首次进入、尚无对话时出现）
  5. 底部对话输入框

### 5.2 预设示例按钮

首次打开时提供 7 个示例，点击即发送，覆盖：转录组差异表达 ×2、蛋白组、通路富集、知识问答、单细胞聚类、空间转录组。

### 5.3 标准对话流程

1. 在输入框用自然语言描述需求（示例见后续章节）
2. 智能体回复中若出现**候选数据集**或**待执行脚本**，按提示确认或取消
3. 任务完成后阅读「结果解读」，产物文件按提示中的路径在磁盘上获取

## 6. 数据获取

### 6.1 两段式流程：检索 → 确认 → 下载

数据获取是**两段式**的：第一段只检索候选，你确认后第二段才真正下载。

```
你:   帮我下载 GSE123456 数据集
智能体: 找到 N 个候选数据集，请在下方选择要下载的项
        [界面出现下拉框：显示推荐理由、描述、元数据、多候选对比表]
你:   （下拉框选中 GSE123456，点击「确认下载」）
智能体: 已下载 GSE123456 → data/raw/geo/GSE123456/GSE123456_series_matrix.txt.gz
```

支持的数据源与触发说法：

| 数据源 | 检索内容 | 下载行为 |
|---|---|---|
| GEO | 数据集（Series）检索 | **下载文件**到 `data/raw/geo/<GSE编号>/` |
| KEGG | 通路 / 基因条目 | 无文件下载，条目内容用于知识问答 |
| UniProt | 蛋白质条目 | 无文件下载，条目内容用于知识问答 |

常见触发说法：`帮我下载 GSE123456`、`帮我找人类肝癌 RNA-seq 数据集`、`检索 XXX 通路`。

### 6.2 下载后的文件位置

```
data/raw/
└── geo/
    └── GSE123456/
        └── GSE123456_series_matrix.txt.gz
```

- 下载会自动记录到 `data/lineage.jsonl`（溯源链）
- 文件已存在时直接复用，不会重复下载
- 下载成功的数据会记入当前会话，后续分析可直接引用

> **重要**：**只有 GEO 会下载文件**。在确认下载的下拉框中请只选择 `geo` 来源的候选；选择 `kegg` / `uniprot` 会产生「知识流数据源，无文件下载」错误。

## 7. 数据分析

### 7.1 支持的分析类型

| 类型 | 说明 | 触发示例 |
|---|---|---|
| `differential_expression` | bulk 差异表达（DESeq2） | 「对这些数据做差异表达分析」 |
| `single_cell` | 固定的过滤前 RNA QC（Seurat/Matrix 读取） | 「对数据做单细胞 QC」 |
| `spatial` | 空间转录组（Visium） | 「做空间转录组分析」 |

**暂不支持**（智能体会明确告知，不会执行）：通路富集、独立火山图等其他分析类型。

### 7.2 脚本人工确认（HITL，默认开启）

分析不是自动执行的——智能体先生成 R 脚本，界面出现**确认卡片**：

```
已生成 R 脚本，请审阅并确认执行

待执行脚本（请审阅）
┌──────────────────────────────────────┐
│ # Method: ...                        │
│ dds <- DESeqDataSetFromMatrix(...)   │
│ ...                                  │
└──────────────────────────────────────┘
[ 确认执行 ]   [ 取消 ]
```

- **确认执行** → 运行脚本，返回结果与解读
- **取消** → 「已取消本次脚本执行」，不产生任何结果

特殊情况：

- **差异表达但没有输入数据** → 智能体提示「需提供数据文件，请先下载或指定输入文件」（请先走第 6 章下载数据）
- **单细胞分析没有输入数据** → 返回 `needs_input`，请提供 10x MTX 目录或原始 counts CSV；不会生成或执行占位脚本
- **空间分析没有输入数据** → 智能体生成一份**占位示例脚本**（输入路径为示例），供你审阅参考；直接确认会因示例输入不存在而失败，建议先准备数据

单细胞输入支持：

- **10x MTX 目录**：`matrix.mtx`、`barcodes.tsv`、`features.tsv`（旧版可用两列 `genes.tsv`）；每个文件可独立使用 `.gz`，同一组件只能存在一个版本。基因 ID 与 barcode 必须唯一，gene symbol 可重复；多模态矩阵只分析 `Gene Expression`。
- **counts CSV**：首列为唯一基因标识，其余列为唯一细胞标识，行为基因、列为细胞。
- counts 必须为有限的非负整数，不能缺失或四舍五入。MatrixMarket 支持 `coordinate integer/real general`，`real` 声明也必须逐值为整数；非法输入在固定 QC 执行前阻断。
- 暂不支持 H5/H5AD、Seurat 对象或 FASTQ。10x 读取保留稀疏矩阵；原输入不会被改写。

单细胞入口只运行固定过滤前 QC，不再生成待确认的自由 R 脚本。统计全部 RNA barcode 的 `nCount_RNA`、`nFeature_RNA`、`percent.mt`，保留原 feature ID / symbol / 唯一读取名称映射。人类线粒体基因按原 symbol 的 `^MT-` 匹配；无法匹配或零 counts 的比例为 `NA`，附原因，不误记为零、不联网补映射。

结果停在 `needs_confirmation` / `qc_review`，展示证据与图表，标明科学未审阅。新 QC 与会话关联成功后可手工填写阈值、生成排除预览并记录这一次过滤选择；记录操作不执行过滤。随后可独立授权“执行此已记录选择的过滤”，仍不标准化、聚类或注释。旧的单细胞脚本确认请求被拒绝；无可信关联的旧 QC 保持只读，需重新发起 QC 才能交互。QC 与固定过滤不调用 LLM 代码生成、自动修复、外部结果解释或共享 KG 写入，持久化证据不回灌模型。

阈值起初为空，没有科学默认值：必填 RNA features 非负整数上下限及 0–100% 的 `percent.mt` 上限，可选 RNA counts 整数上下限。全部条件 AND、包含等号。预览显示预计保留/排除量及全部重叠原因，原因数不能直接相加；逐 barcode 标志保存在本地 `previews/<preview_id>/selection.csv`，不改变 counts 或 QC 文件。

记录前须填写本地选择依据。编辑阈值禁用旧预览的批准；新预览使旧未批准版本失效，输入/QC/版本变化要求重新 QC 或预览。NA、零 counts 或零保留量阻止批准。批准事实、确切参数与依据在刷新后仍可读取，重复批准不产生重复消息。**记录选择不代表科学认可，也不是下游执行授权。**会话关联不是用户鉴权，本地 SQLite 原型不能直接公开部署。

固定过滤执行前再次核对当前已批准预览、原输入/QC/版本哈希，并从 QC 逐 barcode 重算选择。只按已批准选择筛选细胞列，保留全部 RNA feature 行（包括全零行），保存 `data/analysis/sc_filter/<attempt_id>/filtered_counts.rds`、feature/barcode 映射、选择、固定脚本、日志、版本与元数据。输入与 QC 原件不覆盖；RDS 是内部稀疏对象，不是跨语言交付或完整复现包。CSV 读取阶段仍有密集内存开销。

每个精确预览/绑定/过滤版本只执行一次，重复点击或刷新只读权威状态。运行中不能修改选择或删除会话；失败保留证据，不自动重跑，需要新的预览与批准。服务重启把未完成任务标为 `interrupted`，不会恢复执行；仅适用于本地单进程。成功停在 `filter_review`，技术完成但科学未复核；阶段完成不等于下游授权，归一化/PCA、聚类/UMAP 仍需分别明确授权，本片不提供注释。原件前后哈希不是不可变快照，仍可能无法发现运行中改动后又恢复的情况。

过滤完成且父输入、QC、选择与产物身份仍有效时，页面另提供“归一化与 PCA · 独立授权”。读取状态或刷新不执行计算；须填写本地授权依据并独立点击执行。固定流程为 LogNormalize（scale factor 10,000）→ vst（最多 2,000 features）→ 仅变异特征 Scale（不回归）→ PCA（seed 42）。实际 PC 数为 `min(30, 细胞数 - 1, 可用特征数 - 1)`，至少需要 3 个细胞和 3 个可用特征；不足或退化失败，不放宽 QC。当前版本限定 R 4.6.1 / Seurat 5.6.0 / SeuratObject 5.4.0 / Matrix 1.7.5 / irlba 2.4.1，其他版本拒绝执行。

PCA 保存对象、特征映射、数值证据与本地诊断图，不改写 counts 或父证据。同一父产物绑定与工具版本的运行、完成、失败或中断记录均不重复执行；阶段运行中不能修改该 run 的选择或删除会话。完成停在 `pca_review` / `unreviewed`，等待人工维度选择复核，不自动选择下游 dims；PCA 阶段本身不运行聚类、UMAP、marker 或注释。只有记录有效维度并再次明确授权后才可运行聚类/UMAP。授权不代表科学认可；逐细胞数据、授权依据和数值表不回灌外部 LLM 或共享 KG。Scale 为稠密数据，本片仅适用于受信本地单进程，不是生产资源隔离或任务队列。

已完成且身份有效的 PCA 下提供“记录下游维度选择 · 不执行”：查看按 PC 顺序的标准差及既有诊断，手工填写 `k` 和本地依据，记录完整前缀 `[1, ..., k]`。表单初始为空，工程范围为 `1 <= k <= actual_pcs`，没有默认 15 或自动推荐。标准差不是全部输入的总方差百分比；肘部和 PC1/2 图不能单独证明科学选择合理。保存只追加本地事实，不运行 R、LLM、KG 或下游计算，科学状态仍未复核。

刷新恢复当前有效记录和历史，不重发保存请求；修改维度或依据产生新记录。父预览、PCA 产物、记录版本或 run revision 变化时旧记录失效，历史仍可查看；恢复旧父选择也不会自动复活旧维度。依据只在本地复核控件显示，不进入聊天消息或模型历史。并发变更冲突需刷新后重新明确选择。

当前维度记录有效且 `k >= 2` 时，completed PCA 下另提供“受控邻居图 / 聚类 / UMAP · 独立授权”。需填写本次作业的本地依据并单独执行；记录维度不等于授权。项目固定使用 20-NN Annoy、Louvain（resolution 0.5，seed 42）和二维 30-NN uwot（cosine，seed 42，单线程），要求所选 PCs 为连续前缀且每行范数有限非零、各列非退化。本片至少 31 细胞；不足时阻断，不缩短邻居数或放宽 QC。细胞数不超过 10,000 时 UMAP 500 epochs，否则 200 epochs。

同一 PCA/维度/协议绑定只执行一次；运行中不能修改该 run 的选择、维度或删除会话。失败和中断保留证据且不自动重跑，重试须记录新的维度选择并再次明确授权。成功停在 `cluster_review` / `unreviewed`，展示固定协议、聚类数量、群大小和带编号的 UMAP 图；不自动运行 markers、不命名细胞类型、不给出科学结论。逐细胞嵌入与数值表、授权依据不进入聊天、外部 LLM 或共享 KG。R 单次执行仍受 300 秒上限；超时如实失败，不自动降参数。

已完成且身份有效的聚类下提供“探索性聚类接受”：查看编号 UMAP、簇大小及限制，明确勾选并填写本地依据。保存只追加绑定此次聚类、协议、产物和 revision 的接受事实，不执行 R，也不代表专家认可。随后另填本次 marker 授权依据并单独点击执行；接受聚类不等于授权 marker。至少需要两簇，每簇及其余合并各不少于 3 细胞；否则阻断，不合并小簇。上游证据或最新接受记录变化时旧结果保留为失效历史，不展示可用候选和图。

固定 marker 使用 RNA 已有 LogNormalize `data`、全部 RNA features、Seurat 5.6.0 + Presto 1.1.0 的全簇 Wilcoxon 快速路径：`max.cells.per.ident=Inf`、`min.pct=0.01`、`logfc.threshold=0.1`、`only.pos=FALSE`、seed 42。`return.thresh=0.01` 筛原始 p 值，表同时保存按全部 RNA 基因数 Bonferroni 校正的 p 值；不能把所有候选宣称为校正后显著。聚类与检验使用同一批细胞，结果只是探索性候选，不是独立生物学验证。版本/分支不符、部分检验失败、输入变化或超时均失败，不抽样、不更换后端、不自动重跑。

成功停在 `marker_review` / `unreviewed`：页面显示每簇候选量（合法零候选不是失败）、每页最多 100 行的候选统计，以及每簇正向 top 5 去重后的点图。完整双向表、绘图数值、参数、脚本、版本、sessionInfo 和日志留在本地。刷新、断联恢复与重复授权只读状态；重启遗留任务标 interrupted，不恢复计算。运行中不能修改上游、重新接受聚类或删除会话。接受/授权依据只在本地控件与数据库，不进入聊天、外部 LLM 或共享 KG；本片不自动命名细胞类型，仍不提供多用户鉴权、任务队列或不可变输入快照。

### 7.3 失败自动修复

差异表达及空间脚本执行失败时，智能体自动携带报错信息让 LLM 改写脚本重试，**最多修复 2 次**（即同一任务最多执行 3 次）。单细胞 QC/过滤/PCA/聚类与 UMAP/marker 失败只保留证据，不自动重跑或修复。R 脚本单次执行超时仍为 300 秒，不是完整任务截止时间。

### 7.4 分析产物

产物写入磁盘；单细胞 QC 另在页面展示只读汇总与图表，本地路径不是下载链接：

| 分析类型 | 产物文件与位置 |
|---|---|
| 差异表达 | `<输入名>.de_results.csv`（列：gene, log2FoldChange, lfcSE, stat, pvalue, padj） |
| 单细胞 QC | `data/analysis/sc_qc/<run_id>/` 内的 `cell_qc.csv`、`features.csv`、`summary.json`、`qc.png`、`script.R`、`versions.txt`、日志及 `metadata.json` |
| 单细胞 PCA | `data/analysis/sc_pca/<attempt_id>/` 内的 `seurat_pca.rds`、`feature_mapping.csv`、`variable_features.csv`、`pca_embeddings.csv`、`pca_loadings.csv`、`pca_stdev.csv`、`pca_diagnostics.png`、固定脚本、参数、版本、日志及元数据；页面仅提供校验后的本地诊断图，不是任意文件下载接口 |
| 单细胞聚类与 UMAP | `data/analysis/sc_clusters/<attempt_id>/` 内的 `seurat_clustered.rds`、`neighbors.rds`、`clusters.csv`、`umap_embeddings.csv`、`cluster_sizes.csv`、`umap_clusters.png`、`summary.json`、`metadata.json`、固定脚本、版本与日志；页面仅提供校验后的本地 UMAP 图 |
| 单细胞 marker | `data/analysis/sc_markers/<attempt_id>/` 内的 `markers.csv`（原 feature ID/symbol 与双向统计）、`cluster_marker_summary.csv`、`marker_dotplot_data.csv`、`marker_dotplot.png`、`summary.json`、`metadata.json`、固定脚本、版本、sessionInfo 与日志；页面仅提供校验后的候选分页和点图，不是逐细胞数据或任意文件下载接口 |
| 空间转录组 | `<输入名>.spatial_clusters.csv`、`<输入名>.spatial_plot.png`（输入为目录时文件名以 `.` 开头） |
| 差异表达 / 空间分析 | `output/capsules/<时间戳>/` 复现胶囊（原始问题、脚本、意图、元数据） |

每次 QC 使用独立输出目录，不覆盖原三件套或先前结果。元数据记录输入路径、大小、运行前后 SHA256、环境版本与状态；哈希一致性检查不是不可变输入快照，也不等于完整复现包。技术完成不等于科学验收。

示例对话：

```
你:   对 GSE123456 做差异表达分析
智能体: （生成脚本，等待确认）
你:   （点击「确认执行」）
智能体: 差异表达分析完成（自动修复 0 次）
        结果解读: 共检测到 X 个基因，其中 Y 个显著差异（padj < 0.05）...
        输出: data/raw/geo/GSE123456/GSE123456_series_matrix.txt.de_results.csv
        复现胶囊: output/capsules/20260926T.../
```

## 8. 知识问答与知识库

### 8.1 直接提问

输入框上方选「知识问答」再提问（前提：已配好 LLM 与 embedding）。该模式只检索一次知识库、生成一次带引用回答，**不读取会话历史、摘要、下载资产或 KG 记忆**：

```
你:   Which markers identify exhausted T cells?
智能体: （结论 + 每条论断的 [E1] 引用 + 来源 + 可展开的检索原文）
```

- 每条论断必须引用本次检索到的证据编号；引用不存在的编号会被判为失败，不静默修补。
- 来源只认 PMID 或 DOI；两者都解析不到的片段（含形状异常的检索结果）不会交给模型，`config.untraceable_chunks` 记录丢弃数。
- 检索预算写在 `config`：`max_evidence`（默认 8 条）、`max_evidence_chars`（单条默认 4000 字）、`timeout_seconds`。
- 检索显式关闭 LightRAG 共享缓存（`config.retrieval_cache="disabled"`），答案生成也不读缓存（`answer_cache="none"`）；生成上限 `answer_max_tokens`（默认 4000）。
- `retrieval_calls`/`answer_llm_calls` 是服务层**发起次数**，不是实际 HTTP 次数：SDK 层遇限流等错误仍会重试，上限见 `answer_sdk_max_attempts`；整轮已超时则不再发起，计数保持 0。
- 徽章固定标「未复核」：证据支持只代表检索片段支持，不代表已人工核实。回答包另存 `session_id` 与 `question_message_id` 便于回溯。
- 论断必须带 `basis`：`fact` 表示证据直接陈述，`inference` 表示由证据推出的判断（界面分别显示「事实」「推断」）；缺失或取值非法整轮判失败。
- 回答正文只允许 http(s) 链接，远程图片不自动加载（改为显示替代文本），避免第三方内容借回答正文加载。
- 断连时该轮仍会落一条 `status="interrupted"` 的回答包，刷新后可见「已中断」；若断连发生在生成之后，已产出的回答与证据会保留，但状态仍记为中断。
- `config` 另记 `lightrag_version`、`workspace`、`source_batch`（当前固定 `unknown`）、`retrieval_ms`、`generation_ms`；`keyword_llm_calls`/`embedding_calls` 因 LightRAG 不暴露真实值而固定为 `unknown`。

### 8.2 文献一键同步（pubmed-etl → 知识库）

```bash
python sync_pubmed.py                 # 导出 pubmed-etl 结果 → 复制到 data/import/ → 只增量导入本次新 CSV
python sync_pubmed.py --no-import     # 只导出，不导入
python sync_pubmed.py --reimport-all  # 忽略台账，全量重导 data/import/ 下所有文件
```

- **台账**：已导入批次记录在 `data/import/import_ledger.json`（按 CSV 文件名记账），重复运行是空操作（`imported: 0`）；删除或清空该文件即触发一次有意的全量重导。
- **输出**：`{'exported': 1, 'imported': 12, 'failed': 0, 'csv': '...'}`。其中 `imported` 是本次**新批次文章数**（首次提交给 LightRAG 的数量），不是扫描数；也不等于知识库实际新增的文档数——LightRAG 内部按内容哈希/文件名去重，且不返回逐文档去重信号，该数字无法观测。

### 8.3 从目录批量导入

目录中的 `*.json` 与 `*.csv` 文件（文献记录：标题、摘要、关键词、MeSH、作者、年份、期刊、PMID、DOI 等字段）会被转为文本写入知识库：

```bash
python -c "from src.knowledge.import_cli import main; raise SystemExit(main(['--dir', 'data/import']))"                       # 增量：跳过台账已记录的文件
python -c "from src.knowledge.import_cli import main; raise SystemExit(main(['--dir', 'data/import', '--reimport-all']))"      # 全量：忽略台账重导所有文件
```

> `python -m src.knowledge.import_cli` 当前无法直接运行（模块缺少 `__main__` 入口，已知限制），请使用上面的写法。目录导入与 8.2 共用同一份台账，重复执行为空操作；输出会分别给出提交总数与新批次篇数。

### 8.4 按需补库

提问时如果答案上下文不足，且问题涉及 KEGG / UniProt 条目，系统会自动抓取相关条目入库（数量与阈值由 [3.4 按需补库](#34-按需补库可选)控制），回复中会注明自动入库的内容。

### 8.5 知识库维护

```bash
python rebuild_kb.py                          # 清空主知识库并按内置清单重建（保留 embedding 锁定文件）
python -m src.knowledge.build_methods_kb      # 重建方法学知识库
```

## 9. 溯源与复现

### 9.1 记录与快照在哪里

- **运行记录**：`.wrroc/<run_id>/workflow.json` —— intent、参数、步骤、输出
- **代码快照**：`snapshots/<run_id>/` —— Git worktree，锁定当次代码
- **复现胶囊**：`output/capsules/<时间戳>/` —— 问题 + 脚本 + 意图 + 元数据
- **数据血缘**：`data/lineage.jsonl` —— 数据下载 → 分析的链路

`run_id` 形如 `run-226e7eeb`，通过 `.wrroc/` 与 `snapshots/` 的目录名查找（界面不直接显示）。

### 9.2 一键复现

```bash
python -m src.cli.replay <run_id>
# 示例：python -m src.cli.replay run-226e7eeb
# 可选：--repo <仓库根目录>（默认当前目录）
```

成功时输出 JSON（含恢复的快照路径），退出码 0；快照不存在等失败情况退出码 1。

**复现动作包括**：检出 `snapshots/<run_id>/` worktree → 若快照内有 `uv.lock` 则执行 `uv sync` → 若有 `renv.lock` 则执行 `R -e "renv::restore()"`。

**不包括**：恢复数据文件、自动重跑分析（需手动在恢复出的工作目录中操作）。

### 9.3 记录产生的条件（重要）

`workflow.json` 与代码快照由关键词意图**回退路径**产生——即 LLM 工具路由未启用（无 LLM 配置）或工具调用异常回退时。LLM 工具路由**正常成功**的对话轮次不会生成 `.wrroc/` 与 `snapshots/` 记录。溯源依赖功能（KG 记忆写入）目前也未在主程序中接线，属已知限制（见第 14 章）。

## 10. 命令行模式

```bash
python -m src.main
```

- 交互式多轮对话；输入 `quit` 或 `exit`（不区分大小写）退出，`Ctrl+C` 同样退出
- 每轮只回显一行文本回复（`智能体: ...`），不展示表格与图表
- **不支持**候选数据集选择、脚本确认等交互动作——涉及这些步骤请改用 Web 界面
- 不配置 LLM 时自动降级为关键词模板模式，仍可演示基础流程

## 11. 文献子项目 pubmed-etl

独立的 PubMed 文献批量下载与清洗工具（人类单细胞与空间/时序方向），位于 `pubmed-etl/`。

```bash
cd pubmed-etl
pip install -r requirements.txt
# 复制 config/.env.example → config/.env，填入 NCBI_API_KEY / NCBI_EMAIL / ETL_LLM_PROVIDER

python main.py                        # 默认 --step all = download → parse → clean
python main.py --step download --query "multi-omics AND human"   # 自定义搜索词
python main.py --step parse --xml-dir data/raw_xml               # 解析已有 XML
python main.py --step validate [--batch]                         # LLM 二次验证（--batch 仅智谱）
python main.py --step import-review                              # 复核结果入库
python main.py --step export                                     # 导出主项目兼容 CSV
python main.py --step pdf | pdf-retry                            # OA 全文下载 / 断点续传
```

| 阶段 | 说明 | 含在默认 `all` 中 |
|---|---|---|
| download / parse / clean | 下载 → 解析 → 硬过滤清洗 | ✅ |
| validate / import-review / export | LLM 验证 / 复核 / 导出 | ❌ 需单独执行 |
| pdf / pdf-retry | 全文 PDF 下载 | ❌ |

主要输出：`pubmed-etl/data/processed/multiomics_lit.db`、`pubmed-etl/data/output/*.csv`、`pubmed-etl/data/pdfs/`。完成后用 `python sync_pubmed.py`（第 8.2 节）送入主项目知识库——导出侧只导出新 PMID（`exported_pmids.txt`），导入侧按台账只读取新 CSV（`data/import/import_ledger.json`），两侧均为增量，重复执行为空操作。

## 12. 开发者评测

```bash
# 工具路由金标回放（无网络，须 100% 通过）
python -m evals.run_eval --scripted

# 真实 LLM 路由准确率（需配置 API Key）
python -m evals.run_eval --live --provider zhipu

# Skill 路由 + KG 写入/查询 + 最佳实践注入专项（本地 Mock，无需网络）
python -m evals.run_eval --p3d
```

- 三种模式互斥；金标用例默认读取 `evals/cases.jsonl`
- 输出 JSON 报告：`{mode, total, passed, accuracy, failures}`，`--p3d` 另含 `sub_reports`
- 通过阈值 `--min-accuracy` 默认 1.0（即全过）
- 评测使用替身对象，**不会**产生真实的 `.wrroc/`、`snapshots/` 或副作用

> `evals.run_eval --help` 目前存在参数格式化 bug 会报错，请直接参照本节命令使用。

## 13. 常见问题 FAQ

**Q：安装时 `rpy2` 编译失败怎么办？**
本项目执行 R 走 subprocess `Rscript`，不依赖 rpy2。可从 `requirements.txt` 删除 `rpy2` 后重新 `pip install -r requirements.txt`。

**Q：提示找不到 `Rscript`？**
安装 R 4.0+ 并确保 `Rscript` 在 PATH 中（或设置 `R_HOME`）。只有警告不阻断安装，但分析功能需要 R。

**Q：知识库写入报 embedding 模型错误？**
知识库首次写入会把模型名锁定到 `knowledge_base/EMBEDDING_MODEL.json`。换模型需删除 `knowledge_base/` 下除该文件外的内容（或整个目录）后重建（`python rebuild_kb.py`）。

**Q：为什么页面上看不到图表 / 表格？**
分析产物以文件形式写到磁盘（第 7.4 节表格）。单细胞 QC 会在页面展示汇总与图表；证据路径仅指向服务器本地文件，当前没有安全下载接口。

**Q：选了 KEGG / UniProt 候选点「确认下载」报错？**
预期行为——只有 GEO 提供文件下载。请在下拉框选择 `geo` 来源的候选。

**Q：如何切换 LLM 供应商？**
改 `.env` 中 `AGENT_LLM_PROVIDER`（zhipu / deepseek / openai）并填对应 `*_API_KEY`，重启应用。

**Q：`python -m src.knowledge.import_cli --dir ...` 无任何输出？**
已知问题：该模块缺少 `__main__` 入口。改用第 8.3 节的 `python -c ...` 写法，或使用 `sync_pubmed.py`。

**Q：找不到历史运行记录？**
见 [9.3 记录产生的条件](#93-记录产生的条件重要)——只有回退路径的轮次会落盘 `.wrroc/` 与 `snapshots/`；`run_id` 以目录名形式存在于这两处。

**Q：分析很慢或超时？**
单次 R 执行上限 300 秒。大规模数据请先在外部预处理为脚本期望的输入格式（如差异表达的 counts 矩阵 CSV）。

## 14. 已知限制

以下为核对代码后确认的当前行为，使用时请注意：

1. **仅 GEO 支持文件下载**；KEGG / UniProt 为知识流数据源（检索入知识库），其候选不可走「确认下载」。
2. **页面不内嵌图表/表格**：分析结果需按路径取磁盘文件。
3. **WRROC / 快照仅在回退路径产生**：LLM 工具路由正常成功时不生成 `.wrroc/`、`snapshots/`（见 9.3）。
4. **KG 记忆与技能路由未接入主程序**：README 中「KG 记忆查询」「技能自固化」「模态路由」当前仅在评测与测试链路中生效，主程序问答界面暂不可用。
5. **CLI 不支持交互确认**：候选选择、脚本确认仅 Web 界面可用。
6. **侧边栏「外部 API 查询」开关**为说明性占位，暂不影响行为。
7. **`import_cli` 无 `__main__` 入口**（见 FAQ 的替代命令）。
8. **`evals.run_eval --help` 报错**，直接按第 12 章命令使用。
9. **`.env.example` 中 `PROXY`、`KEGG_API_KEY`、`EMBEDDING_MODEL` 当前代码未引用**，配置无效。
10. **「将某数据集写入知识库」暂无界面入口**：可用第 8.3 节的目录导入，或依赖 8.4 按需补库。
11. **REPL / README 中的部分示例为规划目标**：以本指南标注 ✅ 的行为为准。

## 15. 文档维护约定

为保持本指南与代码同步：

1. **分工**：README = 项目概览与功能列表；本指南 = 详细操作手册。功能级新增请在 README 一句话列出，并在本指南补完整章节。
2. **触发更新的变更点**（出现以下改动时必须同步本指南）：
   - 新增/修改用户可见命令、CLI 参数、`.env` 配置项
   - 新增/调整分析类型、产物文件命名
   - UI 布局、按钮文案、确认流程变化
   - 已知限制的新增、修复或移除（第 14 章逐条核对）
3. **事实优先**：内容以代码实际行为为准；写入指南的命令应实际运行验证（更新「最后核对日期」）。
4. **保持结构**：更新后检查目录锚点与 README 跳转链接仍然有效。

