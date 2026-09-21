@echo off
REM Human Multiomics Analysis Agent Setup Script (Windows)

echo Starting setup for Human Multiomics Analysis Agent...

REM Check Python version
python --version
if errorlevel 1 (
    echo Error: Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

REM Create virtual environment
if not exist "venv" (
    echo Creating Python virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install Python dependencies
echo Installing Python dependencies...
pip install --upgrade pip
pip install -r requirements.txt

REM Install R dependencies (if R is available)
where Rscript >nul 2>nul
if %errorlevel% equ 0 (
    echo Installing R dependencies...
    Rscript -e "if (!requireNamespace('renv', quietly = TRUE)) install.packages('renv')"
    Rscript -e "renv::restore()"
) else (
    echo Warning: R not installed, some analysis features may not be available
)

REM Initialize knowledge base directories
if not exist "knowledge_base" mkdir knowledge_base
if not exist "cache" mkdir cache
if not exist "metadata" mkdir metadata

REM Copy config file
if not exist ".env" (
    copy .env.example .env
    echo Please edit .env file to configure API keys
)

echo Setup completed!
echo Run modes:
echo   CLI mode: python -m src.main
echo   Web UI: streamlit run src/ui/app.py
pause