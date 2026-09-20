#!/bin/bash

# 人类多组学分析智能体设置脚本

set -e

echo "开始设置人类多组学分析智能体..."

# 检查 Python 版本
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python 版本: $python_version"

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "创建 Python 虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装 Python 依赖
echo "安装 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 安装 R 依赖（如果 R 可用）
if command -v Rscript &> /dev/null; then
    echo "安装 R 依赖..."
    Rscript -e "if (!requireNamespace('renv', quietly = TRUE)) install.packages('renv')"
    Rscript -e "renv::restore()"
else
    echo "警告: R 未安装，某些分析功能可能不可用"
fi

# 初始化知识库目录
mkdir -p knowledge_base
mkdir -p cache
mkdir -p metadata

# 复制配置文件
if [ ! -f "config/.env" ]; then
    cp config/.env.example config/.env
    echo "请编辑 config/.env 文件配置 API 密钥"
fi

echo "设置完成！"
echo "运行方式："
echo "  命令行模式: python -m src.main"
echo "  Web 界面: streamlit run src/ui/app.py"
