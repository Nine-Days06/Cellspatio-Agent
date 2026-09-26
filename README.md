# CellSpatio 单细胞与时空组学分析智能体

交互式人类单细胞与空间/时序组学数据分析与知识问答系统，基于 LightRAG 和 Python + R 混合架构。

## 功能特性

- **引导式分析**：自然语言驱动的人类单细胞与空间/时序组学数据分析
- **知识增强**：基于 RAG + 知识图谱的专业问答
- **分析覆盖**：bulk 差异表达、单细胞聚类/注释、空间转录组；知识问答跨组学
- **脚本确认**：LLM 生成 R 脚本人工确认后执行，失败自动修复（≤2 次）
- **单细胞/时空**：Seurat / Visium 流程
- **混合架构**：Python 控制 + R 分析，发挥各自优势
- **数据获取**：GEO/KEGG/UniProt 公共数据库检索、确认、下载与入库
- **工具路由**：LLM tool-calling 选择分析/检索/知识工具（schema 强制参数），失败自动回退关键词意图路径
- **工作流记录**：自动生成 `workflow.json`（intent/params/steps/outputs），落盘 `.wrroc/<run_id>/`
- **溯源体系**：WRROC + Lineage 图谱，输入→代码→结果全链路可追踪
- **快照分支**：Git worktree 自动创建 `snapshots/<run_id>/`，锁定代码+数据+环境
- **一键复现**：`python -m src.cli.replay <run_id>` 恢复 worktree、数据、环境、结果
- **可扩展**：支持外部 API 集成和模块化扩展
- **KG 记忆体系**：WorkflowRecorder → LightRAG 增量写入，自然语言查询历史运行/步骤/输出
- **技能自固化**：执行→记忆→检索→优化闭环，同类任务自动获得最佳实践
- **技能平台**：Manifest/Base/Registry/Loader 四件套，热重载毫秒级迭代
- **模态感知路由**：TaskClassifier + ModalRouter，Skill 分发 + 回退链 + KG 注入
- **Eval v2**：Skill路由/KG写入/KG查询/最佳实践注入全自动化评测

## 快速开始

### 安装

```cmd
REM 克隆项目
git clone https://github.com/Nine-Days06/cellspatio-agent.git
cd cellspatio-agent

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

# PubMed/NCBI/KEGG
NCBI_API_KEY=your_key
NCBI_EMAIL=your_email
KEGG_API_KEY=your_key
```

### 运行

**命令行模式：**
```bash
python -m src.main
```

**Web 界面：**
```bash
streamlit run src/ui/app.py
```

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
│   ├── config.py        # 配置（LLM 供应商、API Key、代理）
│   ├── logger.py        # 统一日志
│   ├── error_handler.py # 全局错误处理
│   ├── performance.py   # 性能监控
│   ├── ui/              # Streamlit 界面
│   │   └── app.py       # Web 应用入口
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
│   │   ├── knowledge_builder.py  # 批量构建
│   │   ├── knowledge_importer.py # 文献导入
│   │   ├── llm_factory.py        # LLM/embedding 工厂
│   │   ├── api_gateway.py        # 外部 API 网关
│   │   └── import_config.py      # 知识库导入配置
│   ├── analysis/        # 分析层
│   │   ├── r_executor.py         # R 执行器
│   │   ├── visualization.py      # 可视化
│   │   └── result_explainer.py   # 结果解释
│   └── data/            # 数据层
│       ├── fetchers/             # 公共数据库 Fetcher（GEO/KEGG/UniProt）
│       ├── registry.py           # Fetcher 注册表
│       ├── storage.py            # 本地存储
│       ├── data_loader.py        # 数据加载
│       ├── metadata_manager.py   # 元数据管理
│       └── cache.py              # 缓存
├── pubmed-etl/          # 独立文献批量下载与清洗工具（单细胞+时空方向）
├── r_scripts/           # R 分析脚本
├── tests/               # 测试
├── docs/                # 文档
├── evals/               # 评测体系
│   ├── cases.jsonl           # 金标用例
│   └── run_eval.py           # 评测运行器 (--scripted/--live/--p3d)
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

# P3d 专项评测（Skill路由+KG写入/查询+最佳实践）
python -m evals.run_eval --p3d

# Scripted 模式（工具路由金标回放）
python -m evals.run_eval --scripted

# Live 模式（真实 LLM 路由准确率）
python -m evals.run_eval --live --provider zhipu
```

### 代码风格
```bash
# 格式化代码
black src/ tests/

# 代码检查
ruff check src/ tests/
```

## 贡献指南

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
4. 创建 Pull Request

## 许可证

MIT License