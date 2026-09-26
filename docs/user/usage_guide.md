# CellSpatio 使用指南

> **文档定位**：[README](../../README.md) 是项目概览与功能列表，本指南是**详细操作手册**，面向实验生物学家与数据分析用户，覆盖安装、配置、三大核心操作（数据获取 / 数据分析 / 知识问答）、溯源复现与常见问题。
>
> 本指南内容以代码实际行为为准。最后核对日期：2026-09-26（参考环境：Windows / Python 3.12.10 / R 4.6.1 / Streamlit 1.64.0）。

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
| `single_cell` | 单细胞聚类 / 注释（Seurat） | 「对数据做单细胞聚类」 |
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
- **单细胞 / 空间分析没有输入数据** → 智能体生成一份**占位示例脚本**（输入路径为示例），供你审阅参考；直接确认会因示例输入不存在而失败，建议先准备数据

### 7.3 失败自动修复

脚本执行失败时，智能体自动携带报错信息让 LLM 改写脚本重试，**最多修复 2 次**（即同一任务最多执行 3 次）。修复过程无需你操作；全部失败则返回报错信息。R 脚本单次执行超时 300 秒。

### 7.4 分析产物

产物是**磁盘上的文件**（不是页面内嵌图表），路径在回复与「结果解读」中给出：

| 分析类型 | 产物文件（输出到输入文件同目录） |
|---|---|
| 差异表达 | `<输入名>.de_results.csv`（列：gene, log2FoldChange, lfcSE, stat, pvalue, padj） |
| 单细胞 | `<输入名>.sc_clusters.csv`（细胞聚类 + UMAP 坐标）、`<输入名>.sc_markers.csv`（标记基因） |
| 空间转录组 | `<输入名>.spatial_clusters.csv`、`<输入名>.spatial_plot.png`（输入为目录时文件名以 `.` 开头） |
| 每次分析 | `output/capsules/<时间戳>/` 复现胶囊（原始问题、脚本、意图、元数据） |

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

知识问答无需任何准备（前提：已配好 LLM 与 embedding）：

```
你:   TP53 在癌症中的作用是什么？
你:   差异表达分析为什么用 DESeq2 而不是 TPM？
智能体: （回答 + 下方列出「来源」引用条目）
```

### 8.2 文献一键同步（pubmed-etl → 知识库）

```bash
python sync_pubmed.py                 # 导出 pubmed-etl 结果 → 复制到 data/import/ → 导入知识库
python sync_pubmed.py --no-import     # 只导出，不导入
```

### 8.3 从目录批量导入

目录中的 `*.json` 与 `*.csv` 文件（文献记录：标题、摘要、关键词、MeSH、作者、年份、期刊、PMID、DOI 等字段）会被转为文本写入知识库：

```bash
python -c "from src.knowledge.import_cli import main; raise SystemExit(main(['--dir', 'data/import']))"
```

> `python -m src.knowledge.import_cli` 当前无法直接运行（模块缺少 `__main__` 入口，已知限制），请使用上面的写法。

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

主要输出：`pubmed-etl/data/processed/multiomics_lit.db`、`pubmed-etl/data/output/*.csv`、`pubmed-etl/data/pdfs/`。完成后用 `python sync_pubmed.py`（第 8.2 节）送入主项目知识库。

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
分析产物以文件形式写到磁盘（第 7.4 节表格），按回复给出的 `output_file` 路径打开即可。页面内图表窗格是预留功能，当前版本不渲染。

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
