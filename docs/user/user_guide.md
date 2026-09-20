# 人类多组学分析智能体用户指南

## 概述

人类多组学分析智能体是一个交互式数据分析助手，帮助研究人员进行人类疾病、组织、细胞的多组学数据分析和知识查询。

支持的组学类型：转录组、蛋白质组、代谢组、基因组、表观组等。

## 系统要求

- Python 3.10 或更高版本
- R 4.0 或更高版本（可选，用于高级统计分析）
- 8GB 以上内存（推荐）
- 10GB 以上磁盘空间

## 安装指南

### Windows 安装

1. 安装 Python 3.10+：
   - 从 python.org 下载并安装
   - 确保添加到 PATH 环境变量

2. 安装 R（可选）：
   - 从 r-project.org 下载并安装
   - 安装 RStudio（可选，用于调试）

3. 克隆项目并运行安装脚本：
```cmd
git clone https://github.com/your-org/multiomics-agent.git
cd multiomics-agent
scripts\setup.bat
```

### macOS/Linux 安装

1. 安装依赖：
```bash
# macOS
brew install python@3.10 r

# Ubuntu/Debian
sudo apt update
sudo apt install python3.10 r-base
```

2. 运行安装脚本：
```bash
git clone https://github.com/your-org/multiomics-agent.git
cd multiomics-agent
chmod +x scripts/setup.sh
./scripts/setup.sh
```

## 使用指南

### 启动应用

**Web 界面（推荐）：**
```bash
streamlit run src/ui/app.py
```
浏览器将自动打开 `http://localhost:8501`

**命令行界面：**
```bash
python -m src.main
```

### 数据分析流程

1. **上传数据**：
   - 在 Web 界面点击"上传文件"
   - 支持格式：CSV、TSV、FASTQ、VCF、FASTA
   - 最大文件大小：100MB

2. **选择分析类型**：
   - 差异表达分析
   - 通路富集分析
   - 可视化分析

3. **查看结果**：
   - 分析结果将显示在界面中
   - 支持下载结果文件
   - 可视化图表可交互

### 知识查询

1. **直接提问**：
   - 在聊天框输入问题
   - 例如："TP53 基因的功能是什么？"
   - 例如："癌症中常见的信号通路有哪些？"

2. **查看回答**：
   - 智能体将从知识库中检索相关信息
   - 提供专业、准确的回答
   - 显示信息来源

### 高级功能

**外部 API 集成：**
1. 在设置中启用外部 API
2. 配置 PubMed API 密钥
3. 智能体将自动查询最新文献

**知识库管理：**
```python
# 通过 Python API 管理知识库
from src.knowledge.lightrag_client import LightRAGClient

client = LightRAGClient("./knowledge_base")
client.insert_document("您的文档内容")
result = client.query("您的问题")
```

## 常见问题

### Q: 分析速度很慢怎么办？
A: 
1. 检查网络连接（如果使用云端 LLM）
2. 减小输入数据规模
3. 使用本地 LLM 模型

### Q: 如何添加新的分析类型？
A: 
1. 在 `r_scripts/` 目录添加 R 脚本
2. 在 `src/analysis/` 添加对应的 Python 模块
3. 在 `src/control/intent_parser.py` 添加关键词识别

### Q: 知识库如何更新？
A: 
1. 使用增量更新：定期运行知识库更新脚本
2. 手动更新：通过 Web 界面上传新文档
3. 自动更新：配置外部 API 定期同步

## 技术支持

- 邮件：support@multiomics-agent.com
- GitHub Issues：https://github.com/your-org/multiomics-agent/issues
- 文档：https://docs.multiomics-agent.com
