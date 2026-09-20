# PubMed ETL - 人类多组学文献处理

PubMed 文献处理工具，专门用于筛选人类多组学相关文献，支持文献下载、清洗、LLM 验证和人工复核。

## 功能

- PubMed 批量下载（人类多组学相关文献）
- 文献硬过滤（年份、摘要长度等）
- 人类多组学相关性判断（癌症、代谢病、神经疾病等）
- LLM 智能验证（支持 OpenAI/DeepSeek/智谱）
- 人工复核
- 多格式导出（JSON/CSV/SQLite）

## 使用方法

1. 安装依赖：`pip install -r requirements.txt`
2. 配置 API 密钥：`export PUBMED_API_KEY=your_key`
3. 运行：`python main.py`
4. 导出文件在 `data/export/` 目录

## 导出格式

导出的文件兼容 multiomics-agent（人类多组学分析智能体）的导入格式。