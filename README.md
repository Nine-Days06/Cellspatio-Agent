# 人类多组学分析智能体

交互式人类多组学数据分析与知识问答系统，基于 LightRAG 和 Python + R 混合架构。

## 功能特性

- **引导式分析**：自然语言驱动的人类多组学数据分析
- **知识增强**：基于 RAG + 知识图谱的专业问答
- **多组学支持**：人类转录组、蛋白质组、代谢组、基因组、表观组等
- **混合架构**：Python 控制 + R 分析，发挥各自优势
- **可扩展**：支持外部 API 集成和模块化扩展

## 快速开始

### 安装

```bash
# 克隆项目
git clone https://github.com/your-org/multiomics-agent.git
cd multiomics-agent

# 运行设置脚本
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### 配置

1. 复制配置文件：
```bash
cp config/.env.example config/.env
```

2. 编辑 `config/.env`，配置 API 密钥：
```
OPENAI_API_KEY=your_api_key
PUBMED_API_KEY=your_pubmed_key
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

### 数据分析
```
用户: 分析我的 RNA-seq 数据的差异表达基因
智能体: 请上传您的数据文件（CSV/TSV格式），我将为您执行差异表达分析。
```

### 知识查询
```
用户: TP53 在癌症中的作用是什么？
智能体: TP53 是一个重要的肿瘤抑制基因...
```

## 项目结构

```
multiomics-agent/
├── src/                 # 源代码
│   ├── ui/              # 用户界面
│   ├── control/         # 控制层
│   ├── knowledge/       # 知识检索层
│   ├── analysis/        # 分析层
│   └── data/            # 数据层
├── r_scripts/           # R 分析脚本
├── tests/               # 测试
├── docs/                # 文档
└── config/              # 配置文件
```

## 开发指南

### 运行测试
```bash
# 单元测试
python -m pytest tests/unit/

# 集成测试
python -m pytest tests/integration/

# 性能测试
python -m pytest tests/performance/
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

MIT License - 详见 [LICENSE](LICENSE) 文件
