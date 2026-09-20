import os
import subprocess
import tempfile

import pytest

from src.analysis.r_executor import RExecutor, RExecutorError


def is_r_available():
    """检查R是否可用"""
    try:
        result = subprocess.run(["Rscript", "--version"], capture_output=True, timeout=5, check=False)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

@pytest.mark.skipif(not is_r_available(), reason="R not installed")
def test_execute_r_script():
    """Test executing R scripts"""
    # 创建临时 R 脚本
    with tempfile.NamedTemporaryFile(mode='w', suffix='.R', delete=False) as f:
        f.write("args <- commandArgs(trailingOnly=TRUE)\n")
        f.write("cat('Hello from R:', args[1], '\\n')\n")
        temp_script = f.name
    
    try:
        from src.analysis.r_executor import RExecutor
        executor = RExecutor()
        result = executor.execute_script(temp_script, ["world"])
        
        assert result.returncode == 0
        assert "Hello from R: world" in result.stdout
    finally:
        os.unlink(temp_script)

@pytest.mark.skipif(not is_r_available(), reason="R not installed")
def test_execute_r_code():
    """Test executing R code directly"""
    executor = RExecutor()
    result = executor.execute_code("print(1 + 1)")
    
    assert result.returncode == 0
    assert "[1] 2" in result.stdout

def test_r_executor_initialization():
    """Test R executor initialization"""
    executor = RExecutor()
    
    assert executor.rscript_path is not None
    assert executor.r_home is not None

def test_execute_script_file_not_found():
    """Test executing non-existent R script"""
    executor = RExecutor()
    
    # 现在 execute_script 会抛出 RExecutorError 而不是 FileNotFoundError
    with pytest.raises(RExecutorError):
        executor.execute_script("nonexistent_script.R")

def test_find_rscript_prefers_r_home(tmp_path, monkeypatch):
    """设置 R_HOME 时优先使用 R_HOME/bin 下的 Rscript"""
    rscript_name = "Rscript.exe" if os.name == "nt" else "Rscript"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rscript_file = bin_dir / rscript_name
    rscript_file.write_text("")

    monkeypatch.setenv("R_HOME", str(tmp_path))

    executor = RExecutor()
    assert executor.rscript_path == str(rscript_file)

def test_find_rscript_falls_back_when_r_home_invalid(tmp_path, monkeypatch):
    """R_HOME 中无 Rscript 时回退到 PATH 查找"""
    monkeypatch.setenv("R_HOME", str(tmp_path / "nonexistent"))

    executor = RExecutor()
    assert os.path.basename(executor.rscript_path).lower() in ("rscript", "rscript.exe")