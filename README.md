# CellSpatio 单细胞与时空组学分析智能体

交互式人类单细胞与空间/时序组学数据分析与知识问答系统，基于 LightRAG 和 Python + R 混合架构。

## 📚 文档

| 文档 | 说明 |
|---|---|
| **[使用指南](./docs/user/usage_guide.md)** | 详细操作手册：安装、配置、数据获取/分析/知识问答、溯源复现、FAQ 与已知限制 |
| [项目间接口规范](./docs/api/inter_project_interface.md) | 主项目与 pubmed-etl 的接口约定 |

> README 为项目概览；日常使用请阅读[使用指南](./docs/user/usage_guide.md)。

## 功能特性

- **引导式分析**：自然语言驱动的人类单细胞与空间/时序组学数据分析
- **知识增强**：基于 RAG + 知识图谱的专业问答
- **跨源基因统一**：各源入库文本统一为 NCBIGene CURIE token（`src/data/gene_map.py`），GEO/KEGG/UniProt/Reactome/HPA/PubMed 的基因实体在 LightRAG 图中跨源合并
- **分析覆盖**：bulk 差异表达、空间转录组；受控单细胞入口覆盖 QC、过滤、PCA、人工维度记录及独立 neighbors / Louvain / UMAP；聚类须人工记录探索性接受，再独立授权全细胞 Presto marker，停在 marker 结果未复核，不自动注释；知识问答跨组学
- **脚本确认**：差异表达与空间分析的 LLM 脚本确认后执行，失败自动修复（≤2 次）；受控单细胞使用固定工具，失败不自动修复或重跑
- **单细胞/时空**：Seurat / Visium 流程
- **混合架构**：Python 控制 + R 分析，发挥各自优势
- **数据获取**：GEO/KEGG/UniProt/Reactome/HPA 公共数据库检索、确认、下载与入库
- **工具路由**：LLM tool-calling 选择分析/检索/知识工具（schema 强制参数），失败自动回退关键词意图路径
- **工作流记录**：自动生成 `workflow.json`（intent/params/steps/outputs），落盘 `.wrroc/<run_id>/`
- **溯源体系**：WRROC + Lineage 图谱，输入→代码→结果全链路可追踪
- **快照分支**：Git worktree 自动创建 `snapshots/<run_id>/`，锁定代码+数据+环境
- **一键复现**：`python -m src.cli.replay <run_id>` 恢复 worktree、数据、环境、结果
- **可扩展**：支持外部 API 集成和模块化扩展
- **UI 层**：FastAPI + SSE 流式 + React 18 对话界面；页头支持浅色／深色切换，浏览器记住选择
- **KG 记忆体系**：WorkflowRecorder → LightRAG 增量写入，自然语言查询历史运行/步骤/输出
- **技能自固化**：执行→记忆→检索→优化闭环，同类任务自动获得最佳实践
- **技能平台**：Manifest/Base/Registry/Loader 四件套，热重载毫秒级迭代
- **模态感知路由**：TaskClassifier + ModalRouter，Skill 分发 + 回退链 + KG 注入
- **Eval v2**：Skill路由/KG写入/KG查询/最佳实践注入全自动化评测

## 快速开始

### 安装

```cmd
REM 克隆项目
git clone https://github.com/Nine-Days06/Cellspatio-Agent.git
cd Cellspatio-Agent

REM 运行设置脚本
setup.bat
```

### 配置

1. 复制配置文件：
```bash
cp .env.example .env
```

2. 编辑 `.env`，配置 API 密钥与供应商：
```
# LLM 供应商切换：deepseek / openai / zhipu（默认 zhipu）
AGENT_LLM_PROVIDER=zhipu
DEEPSEEK_API_KEY=your_key
ZHIPU_API_KEY=your_key

# PubMed/NCBI（KEGG 走免费接口，无需 key）
NCBI_API_KEY=your_key
NCBI_EMAIL=your_email

# Ollama embedding 模型（未设时默认 bge-m3）
# EMBEDDING_MODEL=bge-m3

# 随程序启动 Ollama：应用启动时后台预热，省掉首次提问约 4.2s 的等待
# （默认 0=关闭，即按需唤起 + 空闲 10 分钟自动关闭）
# 开启后不再空闲自动关闭，期间常驻约 1.4GB 内存；应用退出时仍会关闭它
# OLLAMA_EAGER_START=0
```

### 运行

**命令行模式：**
```bash
python -m src.main
```

**Web 界面：**
```bash
python -m src.api
```
浏览器打开 `http://127.0.0.1:8600`

### 便携客户端（免登录，数据在本地）

```powershell
.\client\build.ps1          # 瘦包：只打应用代码
.\client\build.ps1 -Fat -RRuntime D:\R-4.6.1   # 胖包：内置 CPython + R/Seurat
```

产物在 `dist/client/`：`cellspatio-thin.zip` / `cellspatio-fat.zip`。

- 数据放便携目录下的 `data/`（`CELLSPATIO_HOME` 可覆盖）。升级时把新包覆盖解压到同一目录即可，`data/` 不在包内、不会被覆盖；不要先删目录。瘦包与胖包要在同一目录运行、或共用同一 `CELLSPATIO_HOME`，才会共享数据。请解压到用户可写目录，不要放 Program Files。
- 胖包解压后双击 `launch.cmd`，无需登录；瘦包先跑 `bootstrap.ps1` 装 Python 依赖并检查 R 4.6.x + Seurat/presto/uwot。
- QC→markers 可离线；知识问答仍需 Ollama 或在线 LLM/embedding。
- 本地默认不写 KG 记忆；`CELLSPATIO_LOCAL_KG=1` 时用文件型 LightRAG，按项目独立目录落盘，不需要 PostgreSQL。
- 无 LLM 时用页头「本地数据」输入 10x 目录或 counts CSV 的绝对路径，直接跑固定 QC；后续过滤、PCA、聚类与 marker 仍逐阶段人工授权。

### 项目与记录管理

- 侧栏按「置顶 → 项目 → 未归类」分组；项目内可新建会话，会话可移动、置顶、归档。归档项目默认隐藏其会话；点侧栏「显示已归档」可置灰查看归档项目/会话并恢复（运行中的会话或项目拒绝归档）。
- 新建项目可只填工作目录，项目名默认取文件夹名，之后可改；只填项目名时工作目录为空。
- 会话/项目的「更多」菜单里显示只读占用汇总（分析产物 + `.wrroc/<run_id>` + `snapshots/<run_id>`；项目另加 `data/projects/<id>` 与 `.wrroc/kg_memory/<id>`），只统计不清理。
- 项目工作目录保存时校验必须是绝对路径、存在、是目录、可写，且不能位于应用数据目录（`data/`、`.wrroc/`、`snapshots/`、`.cellspatio/`）内；本地数据对话框用它预填输入框，`records export` 未给 `--out` 时默认写到该目录（不可用时回退数据根）。
- 新分析产物落 `<数据目录>/data/projects/<project_id>/analysis/...`；未归类会话仍落 `data/analysis/...`。旧 run 保持数据库记录的路径，不迁移。
- 硬删除只走 CLI，不提供 HTTP/UI 删除项目入口：
  ```powershell
  python -m src.cli.records delete session <session_id> --dry-run
  python -m src.cli.records delete session <session_id> --yes
  python -m src.cli.records delete project <project_id> --cascade --dry-run
  python -m src.cli.records delete project <project_id> --cascade --yes
  ```
- 导出会话/项目记录为 zip（清单含路径与大小；加 `--hash` 才逐文件计算 SHA256，不复制产物）：
  ```powershell
  python -m src.cli.records export session <session_id> --out record.zip
  python -m src.cli.records export session <session_id> --hash --out record.zip
  python -m src.cli.records export project <project_id> --redact --out project.zip
  ```
- 证据卡（本地私有，只含聚合统计与哈希，不含 barcode/坐标/样本 ID/绝对路径）：
  ```powershell
  python -m src.cli.evidence export qc <session_id> <run_id>
  python -m src.cli.evidence export cluster <session_id> <run_id>
  python -m src.cli.evidence export pca <session_id> <run_id>
  python -m src.cli.evidence export marker <session_id> <run_id>
  python -m src.cli.evidence export qa <session_id> <answer_id>
  python -m src.cli.evidence review qc <session_id> <run_id> <preview_id> --reason "阈值过宽"
  python -m src.cli.evidence review cluster <session_id> <run_id> <cluster_attempt_id> --reason "簇不稳定"
  python -m src.cli.evidence review marker <session_id> <run_id> <marker_attempt_id> --decision accepted --reason "候选一致"
  python -m src.cli.evidence validate docs/examples/evidence-cards/qc.json
  ```
- 公共证据最小闭环（独立 `<data>/public_evidence/`；预览 → 显式同意 → 隔离区 → 双签 → 检索 → 撤销）：
  ```powershell
  python -m src.cli.evidence_governance preview docs/examples/evidence-cards/qc.json --actor alice --out preview.json
  python -m src.cli.evidence_governance contribute preview.json --consent
  python -m src.cli.evidence_governance review <evidence_id> --actor dora --role domain_reviewer --action domain_pass --check method_complete=true --check claim_matches_observation=true --check limitations_present=true
  python -m src.cli.evidence_governance review <evidence_id> --actor carl --role compliance_reviewer --action compliance_pass --check privacy_scan_passed=true --check license_and_attribution_present=true --check no_raw_data=true --check small_sample_ok=true
  python -m src.cli.evidence_governance search --query QC
  python -m src.cli.evidence_governance withdraw <evidence_id> --actor alice --role contributor --reason "发现方法错误"
  ```

- 公共证据 HTTP 服务（独立进程；账号与证据库都在 `<data>/public_evidence/`）：
  ```powershell
  python -m src.cli.evidence_accounts --root data create alice --role contributor
  python -m src.public_api --root data          # 默认 127.0.0.1:8700
  python -m src.cli.evidence_governance login --server http://127.0.0.1:8700 --username alice
  python -m src.cli.evidence_governance upload preview.json --server http://127.0.0.1:8700 --token <token> --consent
  python -m src.cli.evidence_governance remote-search --server http://127.0.0.1:8700 --query QC
  python -m src.cli.evidence_governance citation <evidence_id> --server http://127.0.0.1:8700
  ```
  复核者浏览器视图：`http://127.0.0.1:8700/review/`（与 API 同进程，登录后看队列、读卡、勾清单、给结论）。

## 使用示例

### 数据获取（两段式：检索 → 确认 → 下载）
```
用户: 帮我下载 GSE123456 数据集
智能体: 找到 3 个候选：GSE123456 - RNA-seq of HCC... 请选择要下载的项
用户: 确认下载 GSE123456
智能体: 已下载 GSE123456 → data/raw/geo/GSE123456/GSE123456_series_matrix.txt.gz
```

### 数据分析（GEO → R 差异表达）
```
用户: 对这些数据做差异表达分析
智能体: 已使用 DESeq2 完成差异表达分析，结果保存至 output.de_results.csv
```

### 知识查询与入库
```
用户: TP53 在癌症中的作用是什么？
智能体: TP53 是重要的肿瘤抑制基因...
用户: 将 GSE123456 写入知识库
智能体: 已写入 1 条文档至 LightRAG 知识库
```

### KG 记忆查询
```
用户: 上次那个差异表达分析用的什么参数？
智能体: run-abc123 使用 DESeq2，FDR=0.05，log2FC=1，输入 GSE123456...
```

### 技能自固化演示
```
用户: 单细胞聚类最佳参数是什么？
智能体: 基于历史 3 次运行，推荐 resolution=0.5, n_neighbors=15（见 run-xxx, run-yyy）
```

### LLM 供应商切换
```
.env 中设置：AGENT_LLM_PROVIDER=zhipu （或 deepseek/openai）
```

## 项目结构

```
cellspatio-agent/
├── src/                 # 源代码
│   ├── main.py          # 入口
│   ├── config.py        # 配置（LLM 供应商、API Key）
│   ├── ui/              # 会话持久化（session_store.py，被 API 层复用）
│   │   └── session_store.py
│   ├── control/         # 控制层
│   │   ├── agent_runtime.py     # tool-calling 运行时（主入口）
│   │   ├── tools.py             # 工具 schema 与系统提示词
│   │   ├── intent_parser.py     # 意图解析（回退路径）
│   │   ├── workflow_manager.py   # 分支执行与 HITL
│   │   ├── workflow_recorder.py  # Workflow 记录器
│   │   ├── wrroc_store.py        # WRROC 落盘存储
│   │   ├── snapshot_manager.py   # Git worktree 快照管理
│   │   ├── replay.py             # 复现接口
│   │   ├── r_script_generator.py # R 脚本生成
│   │   ├── kg_memory.py          # KG Memory: Workflow→LightRAG 增量写入
│   │   ├── kg_query.py           # KG Query: 自然语言查询接口
│   │   ├── router.py             # ModalRouter: 任务分类→Skill分发
│   │   └── classifier.py         # TaskClassifier: 关键词/规则分类
│   ├── skills/          # 技能平台
│   │   ├── base.py        # SkillBase: 技能接口+自动记忆+最佳实践
│   │   ├── loader.py      # SkillLoader: 动态加载+热重载
│   │   ├── registry.py    # SkillRegistry: 技能清单+版本+依赖
│   │   └── manifest.py    # SkillManifest: 元数据+依赖+IO Schema
│   ├── schemas/         # Schema 定义
│   │   ├── workflow.py          # Workflow JSON Schema
│   │   └── lineage.py           # Lineage 图谱模型
│   ├── knowledge/       # 知识检索层
│   │   ├── lightrag_client.py    # LightRAG 封装
│   │   ├── qa.py                 # 受控知识问答（五阶段 + 证据绑定 + 复核）
│   │   ├── prompt_profile.py     # 领域提示词加载与注入（entity_types/domain_rules）
│   │   ├── prompts/              # 领域提示词文件（英文实体类型 + 抽取规则）
│   │   ├── knowledge_builder.py  # 批量构建
│   │   ├── knowledge_importer.py # 文献导入
│   │   ├── llm_factory.py        # LLM/embedding 工厂
│   │   └── article_text.py       # 文章转文本公共函数
│   ├── analysis/        # 分析层
│   │   ├── r_executor.py         # R 执行器
│   │   ├── qc_preview.py         # QC 阈值决策预览与证据校验
│   │   ├── sc_qc.py              # 受控单细胞 QC
│   │   ├── sc_filter.py          # 受控单细胞过滤
│   │   ├── sc_pca.py             # PCA + 人工维度记录
│   │   ├── sc_cluster.py         # neighbors/Louvain/UMAP + 人工接受
│   │   ├── sc_markers.py         # Presto marker（结果须人工复核）
│   │   ├── sc_marker_reviews.py  # Marker 复核绑定与视图
│   │   ├── visualization.py      # 可视化
│   │   └── result_explainer.py   # 结果解释
│   └── data/            # 数据层
│       ├── tenx.py              # 10x 目录三件套校验
│       ├── fetchers/             # 公共数据库 Fetcher（GEO/KEGG/UniProt/Reactome/HPA）
│       ├── gene_map.py           # 基因 ID 统一（NCBI gene_info → NCBIGene CURIE）
│       ├── pubtator.py           # PubTator3 PMID→基因 CURIE 取数与缓存
│       ├── registry.py           # Fetcher 注册表
│       └── storage.py            # 本地存储
├── web/                # React 18 前端（Vite + TS + Tailwind 4）
│   ├── src/pages/      # 页面（ChatPage 对话页）
│   ├── src/components/ # 组件（Sidebar/MessageList/ConfirmCard/ChartBlock）
│   ├── src/lib/        # API/SSE 客户端与类型
│   ├── src/store/      # 状态（zustand chat store）
│   └── dist/           # 构建产物（npm run build，由 FastAPI 挂到 /）
├── client/              # 便携客户端（launch/bootstrap/build；瘦包与胖包）
├── pubmed-etl/          # 独立文献批量下载与清洗工具（单细胞+时空方向）
├── tests/               # 测试
├── docs/                # 文档
├── evals/               # 评测体系
│   ├── cases.jsonl           # 金标用例
│   ├── run_eval.py           # 评测运行器 (--scripted/--live/--p3d)
│   ├── qa_cases.jsonl        # 知识问答跑批用例（12 例）
│   ├── qa_review.py          # 问答复核表单格式化与记录
│   └── run_qa_eval.py        # QA 跑批评测（--only/--out，质量门槛 quality_claim_allowed）
├── build_pubtator_cache.py # 预计算 PubTator3 PMID→基因 CURIE 缓存（导入前执行一次）
├── canary_extract.py       # 真实抽取金丝雀：小样本入库读实体、判定抽取质量
├── rebuild_kb.py           # 知识库重建（KGML/Reactome/HPA）
├── sync_pubmed.py          # 文献一键同步（pubmed-etl 导出 → 主项目导入）
├── start_pg.ps1            # 项目内 PostgreSQL 启停（端口 5433，测试库 rag_test）
├── setup.bat            # Windows 一键安装脚本
└── .env.example         # 环境变量示例
```

## 开发指南

### 运行测试
```bash
# 单元测试
python -m pytest tests/unit/

# 集成测试
python -m pytest tests/integration/

# 全量测试
python -m pytest tests/

# Windows 单测门禁（自动启停项目内 PG + pytest 临时目录）
.\run_unit_tests.ps1

# P3d 专项评测（Skill路由+KG写入/查询+最佳实践）
python -m evals.run_eval --p3d

# Scripted 模式（工具路由金标回放）
python -m evals.run_eval --scripted

# Live 模式（真实 LLM 路由准确率）
python -m evals.run_eval --live --provider zhipu
```

### 代码风格与提交门禁
```bash
# 安装开发工具（ruff/pytest；setup.bat 已自动包含）
pip install -e .[dev]

# 格式化代码
ruff format src/ tests/ pubmed-etl/ *.py

# 代码检查
ruff check src/ tests/ pubmed-etl/ *.py
```

提交前门禁与分支约定见 [CONTRIBUTING.md](./CONTRIBUTING.md)：`main`/`dev` 禁止直接提交，`feature/*` 从 `dev` 分出并合回 `dev`。

## 贡献指南

完整协作约定（分支模型、Conventional Commits、本地门禁、PR 模板）见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 许可证

本项目暂未设置许可证，暂保留所有权利。未经许可，请勿分发、修改或用于商业用途。
