@echo off
REM 人类多组学分析智能体设置脚本 (Windows)

echo 开始设置人类多组学分析智能体...

REM 检查 Python 版本
python --version
if errorlevel 1 (
    echo 错误: 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 创建虚拟环境
if not exist "venv" (
    echo 创建 Python 虚拟环境...
    python -m venv venv
)

REM 激活虚拟环境
call venv\Scripts\activate.bat

REM 安装 Python 依赖
echo 安装 Python 依赖...
pip install --upgrade pip
pip install -r requirements.txt

REM 安装 R 依赖（如果 R 可用）
where Rscript >nul 2>nul
if %errorlevel% equ 0 (
    echo 安装 R 依赖...
    Rscript -e "if (!requireNamespace('renv', quietly = TRUE)) install.packages('renv')"
    Rscript -e "renv::restore()"
) else (
    echo 警告: R 未安装，某些分析功能可能不可用
)

REM 初始化知识库目录
if not exist "knowledge_base" mkdir knowledge_base
if not exist "cache" mkdir cache
if not exist "metadata" mkdir metadata

REM 复制配置文件
if not exist ".env" (
    copy .env.example .env
    echo 请编辑 .env 文件配置 API 密钥
)

echo 设置完成！
echo 运行方式：
echo   命令行模式: python -m src.main
echo   Web 界面: streamlit run src/ui/app.py
pause
