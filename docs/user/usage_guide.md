# CellSpatio 使用指南

> **文档定位**：[README](../../README.md) 是项目概览，本指南是详细操作手册，面向实验生物学家
> 与数据分析用户，覆盖安装、配置、Web 界面、数据获取、分析、知识问答、记录与限制。
>
> 最后核对日期：2026-11-25（本次更新流式行为与证据面板；Windows / Python 3.12 / R 4.6.1 / FastAPI + React）。

## 目录

1. [简介与适用人群](#1-简介与适用人群)
2. [安装与启动](#2-安装与启动)
3. [配置](#3-配置)
4. [Web 界面](#4-web-界面)
5. [数据获取](#5-数据获取)
6. [数据分析](#6-数据分析)
7. [知识问答与知识库](#7-知识问答与知识库)
8. [项目、记录与数据位置](#8-项目记录与数据位置)
9. [常见问题](#9-常见问题)
10. [当前限制](#10-当前限制)
11. [文档维护](#11-文档维护)

## 1. 简介与适用人群

CellSpatio 用自然语言帮助你完成单细胞与空间/时序组学分析：检索公共数据、生成受控 R 脚本、
记录人工决策，并基于文献知识库回答问题。它不会自动替代科学判断；关键阶段需要你明确授权。

**适合**：需要探索公共数据、做单细胞/空间分析、或查文献证据的实验研究者与数据分析人员。

**你需要具备**：

- Windows/macOS/Linux 基本操作；
- 从源码运行需要 Python 3.10+ 与 R 4.6.x（受控单细胞链路有固定版本要求）；
- 一个 LLM 供应商的 API Key（智谱 / DeepSeek / OpenAI 任一）；
- 知识问答需要 Ollama（默认 `bge-m3`）与一个 LLM 供应商。

**当前边界**：

- 聚类是探索性结果，marker 是未复核候选，不自动命名细胞类型。
- 受控单细胞每个阶段都要人工授权，失败不会自动重跑或换参数。
- 本地 Web 服务是单进程、单机原型，不是多用户生产服务。

## 2. 安装与启动

### 2.1 便携客户端（推荐，Windows）

便携客户端有两种包：

| 包 | 包含内容 | 首次启动 |
|---|---|---|
| **在线安装包** | 应用代码；不含 Python/R 运行时 | 运行 `bootstrap.ps1` 安装 Python 依赖并检查 R，再运行 `launch.cmd` |
| **内置运行时包** | 应用代码 + CPython + R/Seurat | 双击 `launch.cmd` |

1. 解压到用户可写目录，不要放在 `Program Files`。
2. 在线安装包需要 Python 3.10+（或 `uv`）以及 R 4.6.x + Seurat/presto/uwot；
   `bootstrap.ps1` 只安装 Python 依赖和检查 R，不会替你安装 R 包。
3. 启动后浏览器打开 `http://127.0.0.1:8600`；数据在便携目录的 `data/`。
4. 升级时把新包覆盖解压到同一目录，`data/` 不会被覆盖；不要先删目录。

便携客户端默认只监听本机回环地址，不需要登录。包内 `README.txt` 说明目录、升级和离线范围。

### 2.2 从源码运行

**Windows 一键安装**：

```cmd
git clone https://github.com/Nine-Days06/Cellspatio-Agent.git
cd Cellspatio-Agent
setup.bat
```

`setup.bat` 会：

1. 检查 Python 3.10+；
2. 创建并激活 `venv`；
3. 安装 `requirements.txt` 与 `pip install -e .[dev]`；
4. 创建 `knowledge_base/`、`cache/`、`metadata/`；
5. 复制 `.env.example` 为 `.env`；
6. 检查 `Rscript`，但**不会安装 R 或 R 包**，也**不会构建前端**。

**手动安装**：

```bash
python -m venv venv
# Windows PowerShell: .\venv\Scripts\Activate.ps1
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
pip install -e .[dev]
cp .env.example .env          # Windows 用 copy .env.example .env
```

**R 包**：受控单细胞链路要求 R 4.6.1、Seurat 5.6.0、SeuratObject 5.4.0、
Matrix 1.7.5、irlba 2.4.1；其他版本会被拒绝执行。源码环境需要自行锁定这些版本，
最省事的方式是使用内置运行时包。通用安装示例：

```r
install.packages(c("Seurat", "uwot", "remotes"), repos = "https://cloud.r-project.org")
remotes::install_github("immunogenomics/presto")
```

**前端**：如果 `web/dist/` 不存在，先构建：

```bash
cd web
npm ci
npm run build
```

### 2.3 启动

**Web 界面（推荐）**：

```bash
python -m src.api
```

默认监听 `127.0.0.1:8600`，启动后自动打开浏览器。若端口被占用或浏览器未自动打开，
手动访问 `http://127.0.0.1:8600`。

**命令行模式**：

```bash
python -m src.main
```

输入 `quit` 或 `exit` 退出，`Ctrl+C` 也可退出。CLI 只回显文本，不提供候选数据集选择和
脚本确认；涉及这些步骤请用 Web 界面。

## 3. 配置

配置文件是项目根目录的 `.env`（便携客户端可用包内环境变量或同一份配置）。

### 3.1 LLM 供应商（分析/问答需要）

```env
# 主项目供应商：zhipu / deepseek / openai（默认 zhipu）
AGENT_LLM_PROVIDER=zhipu
ZHIPU_API_KEY=your_key

# 其他供应商按需填写
DEEPSEEK_API_KEY=your_key
# DEEPSEEK_MODEL=deepseek-flash
OPENAI_API_KEY=your_key
# OPENAI_MODEL=gpt-4
```

`LLM_PROVIDER` 是旧的兼容字段；同时存在时 `AGENT_LLM_PROVIDER` 优先。
LLM 用于意图/工具路由、脚本生成与失败修复、知识库抽取和问答；受控单细胞的固定 QC/过滤/
PCA/聚类/marker 不依赖 LLM 生成代码。

### 3.2 知识库 embedding

```env
# Ollama 地址与 embedding 模型
# OLLAMA_URL=http://127.0.0.1:11434
# EMBEDDING_MODEL=bge-m3

# 自动唤起 Ollama（默认开启）
# OLLAMA_AUTOSTART=1
# 缺少模型时自动下载（默认关闭，模型下载是用户资产决策）
# OLLAMA_AUTO_PULL=0
# 空闲关闭本应用启动的 Ollama（分钟，<=0 不自动关闭）
# OLLAMA_IDLE_MINUTES=10
# 随程序启动预热（默认关闭）
# OLLAMA_EAGER_START=0
```

知识库首次写入会把 embedding 模型名锁定到 `knowledge_base/EMBEDDING_MODEL.json`；
换模型需要清库重建（见第 7.3 节）。没有 Ollama 时，数据下载、受控单细胞分析和脚本确认
仍可用，知识库写入/问答不可用。

### 3.3 公共数据库

```env
NCBI_API_KEY=your_ncbi_api_key
NCBI_EMAIL=your_email@example.com
```

NCBI 配置用于提高 GEO/PubMed 检索与下载配额。KEGG/UniProt 条目只进入知识流，不下载文件。

### 3.4 脚本确认与本地模式

```env
# 生成的 R 脚本需要人工确认后才执行（默认开启）
SCRIPT_REQUIRE_CONFIRM=1

# 便携客户端默认关闭 KG 记忆；置 1 使用文件型 LightRAG（不需要 PostgreSQL）
# CELLSPATIO_LOCAL_KG=1

# 问答上下文不足时自动补库（默认关闭；置 1 开启）
# KB_AUTO_INGEST=1
# LAZY_INGEST_MIN_CONTEXT=300
# LAZY_INGEST_MAX_ASSETS=3
```

## 4. Web 界面

### 4.1 布局

- **侧栏**：置顶/项目/未归类会话；新建项目、新建会话、搜索、显示已归档；
  底部显示知识库状态与运行环境（Rscript、知识库目录、Ollama）。
- **页头**：当前会话标题、项目名、运行状态、本地数据入口、浅色/深色主题切换。
- **消息区**：对话历史、分析图表/证据卡、脚本与候选数据确认卡。
- **输入区**：模式切换（智能对话 / 严格文献深答）、输入框、发送/停止。

### 4.2 起始提示

首次进入且没有对话时，页面提供 3 个提示：

1. 检查单细胞数据；
2. 查阅文献证据；
3. 规划空间组学分析。

点击提示只会填入问题，不会直接发送；确认后再按 Enter 或点发送。

### 4.3 标准流程

1. 在侧栏新建项目或会话；项目可绑定本地工作目录。
2. 在输入区选择「智能对话」或「严格文献深答」。
3. 分析请求出现脚本/候选数据确认卡时，先审阅再确认。
4. 单细胞按 QC → 过滤 → PCA → 聚类/UMAP → marker 阶段推进，每步都要人工授权。
5. 结果、证据和运行记录保存在本地；页面只展示校验后的摘要和关键图表。

输入框支持 Enter 发送、Shift+Enter 换行；运行中可点「停止」，但正在执行的工具步骤
可能无法立即中断。

## 5. 数据获取

### 5.1 两段式流程：检索 → 确认 → 下载

第一段只检索候选，你确认后第二段才真正下载。例如：

```text
你: 帮我下载 GSE123456 数据集
智能体: 找到候选数据集，请在下方选择要下载的项
你: 选中 GSE123456，点击确认下载
智能体: 已下载 GSE123456
```

| 数据源 | 检索内容 | 下载行为 |
|---|---|---|
| GEO | 数据集（Series） | 下载文件到 `data/raw/geo/<GSE编号>/` |
| KEGG | 通路/基因条目 | 无文件下载，内容进入知识流 |
| UniProt | 蛋白质条目 | 无文件下载，内容进入知识流 |

只有 GEO 提供文件下载；KEGG/UniProt 候选不要点「确认下载」。

### 5.2 文件位置

```text
data/raw/geo/GSE123456/
  GSE123456_series_matrix.txt.gz
```

- 已存在的文件会复用，不重复下载。
- 下载会记录到 `data/lineage.jsonl`。
- 下载成功的数据会记入当前会话，后续分析可直接引用。

## 6. 数据分析

### 6.1 差异表达与空间分析

支持 bulk 差异表达（DESeq2）和空间转录组（Visium）流程。智能体先生成 R 脚本，页面出现
确认卡后才会执行：

- **确认执行**：运行脚本，返回结果与解读；
- **取消**：不产生结果；
- 执行失败时，差异表达/空间脚本最多自动携带报错修复 2 次；
- 单次 R 执行上限 300 秒，超时按失败处理。

待确认脚本已关联本地准备记录时，刷新页面或重启应用后会校验会话、脚本和参数，
校验通过的卡片仍可确认。旧卡片或绑定不匹配的卡片会过期，需要重新发起分析。

没有输入数据时：

- 差异表达返回 `needs_input`，先下载或指定输入文件；
- 单细胞返回 `needs_input`，请提供 10x MTX 目录或 counts CSV；
- 空间分析可能给出占位示例脚本，直接确认会因示例输入不存在而失败，请先准备数据。

### 6.2 受控单细胞

输入只支持：

- **10x MTX 目录**：`matrix.mtx`、`barcodes.tsv`、`features.tsv`（旧版可用两列
  `genes.tsv`），每个文件可独立 `.gz`；
- **counts CSV**：首列唯一基因标识，其余列唯一细胞标识，行为基因、列为细胞。

counts 必须是有限的非负整数，不能缺失或四舍五入；不支持 H5/H5AD、Seurat 对象或 FASTQ。

| 阶段 | 你需要做什么 | 系统做什么 |
|---|---|---|
| QC | 提供输入；查看细胞数、features、线粒体比例等 | 固定 QC，不做 LLM 代码生成 |
| 过滤 | 填写阈值、填写本地依据、预览并批准 | 只按批准选择过滤细胞，保留 RNA feature 行 |
| PCA | 查看版本与诊断，独立授权 | 固定 LogNormalize/vst/Scale/PCA；人工记录 `k` |
| 聚类/UMAP | 记录维度后独立授权 | 固定 20-NN Annoy + Louvain + uwot；至少 31 细胞 |
| marker | 接受探索性聚类，再单独授权 marker | Presto 全簇 Wilcoxon 候选；不自动注释 |

关键规则：

- 每个阶段都要明确授权；保存选择不等于执行，也不等于科学认可。
- QC 阈值没有科学默认值；NA、零 counts 或零保留量会阻止批准。
- 过滤/ PCA/聚类/ marker 失败只保留证据，不自动重跑或换参数。
- marker 结果停在 `marker_review / unreviewed`，每页候选最多 100 行，不自动命名细胞类型。
- 受控单细胞固定版本：R 4.6.1、Seurat 5.6.0、SeuratObject 5.4.0、Matrix 1.7.5、
  irlba 2.4.1；其他版本会被拒绝执行。

### 6.3 产物位置

未归类会话在 `data/analysis/`，项目会话在 `data/projects/<project_id>/analysis/`。

| 分析 | 主要产物 |
|---|---|
| 差异表达 | `de/<run_id>.de_results.csv`（gene、log2FoldChange、pvalue、padj 等） |
| 单细胞 QC | `sc_qc/<run_id>/`：`cell_qc.csv`、`summary.json`、`qc.png`、脚本、版本与元数据 |
| 单细胞过滤 | `sc_filter/<attempt_id>/`：`filtered_counts.rds`、映射、选择、日志与元数据 |
| PCA | `sc_pca/<attempt_id>/`：`seurat_pca.rds`、embeddings/loadings、诊断图、脚本与版本 |
| 聚类/UMAP | `sc_clusters/<attempt_id>/`：`seurat_clustered.rds`、clusters/UMAP、UMAP 图与元数据 |
| marker | `sc_markers/<attempt_id>/`：`markers.csv`、候选汇总、点图数据、脚本与 `sessionInfo` |
| 空间 | `<run_id>.spatial_clusters.csv`、`<run_id>.spatial_plot.png` |
| 复现胶囊 | `capsules/<时间戳>/`：原始问题、脚本、意图与元数据 |

差异表达/空间/胶囊都写在上述会话根下（`de/`、`spatial/`、`capsules/`），
不再写回输入文件所在目录或仓库根 `output/`。

页面展示的是校验后的摘要与关键图；完整表格和对象留在本地磁盘，不提供任意文件下载接口。

### 6.4 溯源与复现

- 运行记录：`.wrroc/<run_id>/workflow.json`；
- 代码快照：`snapshots/<run_id>/`（Git worktree）；
- 复现胶囊：`data/analysis/capsules/<时间戳>/`（项目会话在 `data/projects/<id>/analysis/capsules/`）；
- 数据血缘：`data/lineage.jsonl`。

差异表达与空间分析等待脚本确认时，只保存 `.wrroc/` 中的准备事实与运行身份，
尚未执行 R，也不触发运行完成时的代码快照或知识图谱写入。
确认请求会恢复并核验原运行，保留准备时间与步骤。成功后的结果文件、已生成空间图
和胶囊文件登记在同一运行中，胶囊保存实际执行成功的脚本，包括自动修复后的版本。
重复确认不会重复执行。执行成功后如果记录或消息写入失败，卡片仍保持已确认，
需要检查本地记录和产物后处理故障。已知执行失败会保留尝试步骤，卡片释放后可由用户
明确重试；取消会核验原准备身份并保存取消事实，取消记录发生临时读写故障时释放本次领取，
用户可明确再次取消。执行结果未知时记录中断，不开放自动
恢复或复制卡重跑。无身份旧卡的当前确认请求会记录本次兼容执行，不推断原准备运行；
刷新恢复仍会使无身份旧卡过期。

```bash
python -m src.cli.replay <run_id>
```

复现会读取已有快照并尽量恢复依赖；不会恢复数据文件或自动重跑分析。命令返回 `restored`
表示只恢复了快照、分析未重跑；返回 `error` 表示未找到快照。运行记录/快照的生成
取决于执行路径，不能把它当作所有对话的完整审计。

## 7. 知识问答与知识库

### 7.1 直接提问

在输入区选择「严格文献深答」再提问。该模式与智能对话共用检索服务，但强制先检索、
只暴露文献检索工具（模型请求其他工具时会被拒绝并提示改调检索），并沿用会话历史
处理指代追问：

```text
你: Which markers identify exhausted T cells?
智能体: 结论 + [E1] 引用 + 可点击展开的「检索原文」
```

- 普通对话逐字显示正文；本轮真正检索过的回合会等工具结果回来后再显示正文，
  避免先写一版又被证据改写。思考过程在两种模式下都照常流式。
- 每条事实必须引用本次检索到的证据编号；引用不存在的编号会重试一次后报校验失败。
- 来源只认 PMID 或 DOI；无法解析的片段不会交给模型。
- 点击正文里的 `[E1]` 会展开对应片段并滚动定位；折叠时只显示前四行，展开后显示完整正文。
- 「检索原文」是平铺的片段列表，「来源」单独列出文献（标题优先，缺失时显示编号）、
  年份与原文链接；同一片段只出现一次，不按文献分组，也没有「未归属来源」。
- 模型可附显式结构化论断；没有显式给出的论断不会由正文臆造。
- 回答正文只允许 http(s) 链接，远程图片不自动加载。
- 检索预算与超时沿用对话链路配置，默认最多 8 条证据；证据包随消息保存，刷新后可展开。
- 没有 LLM/embedding 时，严格文献深答不可用；受控单细胞固定流程不受影响。

### 7.2 文献同步与导入

```bash
python sync_pubmed.py                 # 导出 pubmed-etl 结果 → 复制到 data/import/ → 增量导入
python sync_pubmed.py --no-import     # 只导出
python sync_pubmed.py --reimport-all  # 忽略台账，全量重导
```

已导入批次记录在 `data/import/import_ledger.json`；重复运行是空操作。pubmed-etl 的
下载、解析和清洗命令见 [pubmed-etl/README.md](../../pubmed-etl/README.md)。

### 7.3 知识库维护

```bash
python rebuild_kb.py                          # 清空主知识库并重建（保留 embedding 锁定文件）
python -m src.knowledge.build_methods_kb      # 重建方法学知识库
```

知识库首次写入会锁定 embedding 模型名。换模型需删除 `knowledge_base/` 下除
`EMBEDDING_MODEL.json` 外的内容（或整个目录）后重建。

## 8. 项目、记录与数据位置

### 8.1 项目与会话

- 侧栏按「置顶 → 项目 → 未归类」分组；项目内可新建会话。
- 没有会话时直接输入并发送，会自动新建会话并选中；新建失败会提示错误并保留输入。
- 会话标题：首轮问答完成后由模型生成短标题；生成失败或没有可用模型时沿用首条提问的前 20 字。
- 会话菜单可「重命名」；手动命名（以及新建时显式传入的标题）不会被后续自动命名覆盖。
- 会话可移动、置顶、归档；有阶段在运行时不能换项目（会 409），移动/置顶/归档前先等当前阶段结束。归档项目默认隐藏其会话，点「显示已归档」可查看并恢复。
- 项目工作目录必须是绝对路径、存在、可写，且不能位于 `data/`、`.wrroc/`、
  `snapshots/`、`.cellspatio/` 内。

### 8.2 数据目录

- 源码运行：项目根目录下的 `data/`、`knowledge_base/`、`.wrroc/`、`snapshots/`；
- 便携客户端：便携目录下的 `data/`，可用 `CELLSPATIO_HOME` 覆盖；
- 项目分析产物：`data/projects/<project_id>/analysis/...`；
- 未归类会话：`data/analysis/...`。

### 8.3 记录导出与删除

硬删除只提供 CLI，建议先 `--dry-run`：

```powershell
python -m src.cli.records export session <session_id> --out record.zip
python -m src.cli.records export project <project_id> --redact --out project.zip
python -m src.cli.records export session <session_id> --hash --out hashed-record.zip
python -m src.cli.records delete session <session_id> --dry-run
python -m src.cli.records delete project <project_id> --cascade --dry-run
```

导出 zip 包含 `record.json`、可读 Markdown 与 `artifacts.csv`，不复制原产物。
清单的 `run_id` 与 `source` 区分已登记的 `wrroc_output`、固定阶段 `stage_directory`
及旧项目目录补录 `directory_discovery`；补录文件的运行身份留空，不根据目录猜测归属。
新动态运行登记的 CSV、实际空间图和脚本胶囊会关联同一运行，目录和单文件登记重叠时
在同一运行中只列一次；不同运行显式登记同一文件时保留各自关联。仅列数据根内现存的
普通文件，排除逃逸路径和符号链接；`--hash` 逐文件计算
当前 SHA256。默认保留路径；`--redact` 会覆盖消息、旧记录以及动态脚本和参数中的路径。
记录格式版本为 `cellspatio-record/1.2.0`。

删除会移动记录到 `.trash` 并清理数据库；确认无误后再去掉 `--dry-run` 并加 `--yes`。

### 8.4 证据卡与公共证据

本地证据卡只含聚合统计与绑定信息，不含 barcode、坐标、样本 ID 或绝对路径：

```powershell
python -m src.cli.evidence export qc <session_id> <run_id>
python -m src.cli.evidence export cluster <session_id> <run_id>
python -m src.cli.evidence export pca <session_id> <run_id>
python -m src.cli.evidence export marker <session_id> <run_id>
```

页面、CLI 复核和导出会重验本地来源与产物；文件缺失、漂移或复核身份失效时，旧选择保留为历史，不能生成当前证据卡。相同历史事实重发不会恢复其有效性；应检查证据并重新明确复核。科学质量不合格的 QC 预览仍可人工拒绝，并在证据身份有效时生成拒绝卡。

公共证据服务的账号、上传、双签、检索、备份和 Caddy/TLS 部署见
[部署说明](../deploy.md)。浏览器入口是 `http://127.0.0.1:8700/review/`。

## 9. 常见问题

**Q：提示找不到 `Rscript`？**
安装 R 4.6.x，把 `Rscript` 加入 PATH，或设置 `R_HOME`。只有警告不阻断安装，但分析功能
需要 R；受控单细胞还要求固定 R/Seurat 版本。

**Q：Web 页面空白或只有 API？**
检查 `web/dist/` 是否存在。源码环境执行 `cd web && npm ci && npm run build` 后重启
`python -m src.api`。

**Q：知识库写入报 embedding 模型错误？**
检查 Ollama 是否运行、`EMBEDDING_MODEL` 是否已下载。模型名首次写入后会锁定；换模型需
清库重建。

**Q：选了 KEGG/UniProt 候选点「确认下载」报错？**
预期行为。只有 GEO 提供文件下载；KEGG/UniProt 只进入知识流。

**Q：分析结果在哪里？**
见第 6.3 节。页面展示摘要和关键图，完整 CSV/RDS/日志在本地分析目录。

**Q：为什么页面显示「未复核」？**
聚类和 marker 结果需要人工复核；技术完成不等于科学认可。

**Q：没有 LLM 能用吗？**
可以走本地数据入口和受控单细胞固定流程；差异表达/空间脚本生成和知识问答需要 LLM。
读取状态或刷新不会触发计算。

**Q：分析很慢或超时？**
单次 R 执行上限 300 秒。大规模数据请先在外部预处理成脚本期望的输入格式。

## 10. 当前限制

- 聚类是探索性结果，marker 是未复核候选，不自动命名细胞类型。
- 受控单细胞不支持 H5/H5AD、Seurat 对象或 FASTQ；只支持 10x MTX 目录和 counts CSV。
- 受控单细胞 PCA/聚类/marker 依赖固定 R 与 Seurat 版本；版本不符会拒绝执行。
- 单次 R 执行 300 秒；受控单细胞失败不自动重跑或降参数。
- 本地 Web 服务是单进程、单机原型，不提供多用户鉴权或任务队列。
- 知识问答依赖 Ollama 与 LLM 供应商；公共证据检索目前不会自动注入知识问答。
- 页面不提供任意文件下载；分析产物需在本地目录获取。
- 运行记录/快照是否生成取决于执行路径，不能替代完整审计。
- Windows 便携客户端是当前主要分发方式；源码方式支持 Windows/macOS/Linux。

## 11. 文档维护

1. **分工**：README = 项目概览与快速开始；本指南 = 详细操作；开发/测试/打包见
   [CONTRIBUTING.md](../../CONTRIBUTING.md)，架构与模块说明见
   [开发文档](../developer/README.md)。
2. **必须同步的改动**：用户可见命令、`.env` 配置、分析类型、产物路径、UI 布局/按钮、
   已知限制。
3. **事实优先**：命令应实际运行验证；更新后修改「最后核对日期」，并检查锚点与链接。
