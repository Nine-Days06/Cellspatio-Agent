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
- **可扩展**：支持外部 API 集成和模块化扩展

## 快速开始

### 安装

```cmd
REM 克隆项目
git clone https://github.com/your-org/cellspatio-agent.git
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
│   │   ├── intent_parser.py      # 意图解析
│   │   ├── workflow_manager.py   # 两段式工作流
│   │   └── r_script_generator.py # R 脚本生成
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
5. 创建 Pull Request

## 许可证

MIT License