import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

class RExecutorError(Exception):
    """R 执行器错误"""

class RExecutor:
    """R 脚本执行器"""
    
    def __init__(self, r_home: str | None = None):
        self.r_home = r_home or os.getenv("R_HOME", "/usr/lib/R")
        self.rscript_path = self._find_rscript()
    
    def _find_rscript(self) -> str:
        """查找 Rscript 可执行文件"""
        # 若 R_HOME 已设置，优先使用其中的 Rscript
        if self.r_home:
            rscript_name = "Rscript.exe" if os.name == "nt" else "Rscript"
            candidate = os.path.join(self.r_home, "bin", rscript_name)
            if os.path.exists(candidate):
                return candidate
        
        # 其次使用 shutil.which 查找
        rscript = shutil.which("Rscript")
        if rscript:
            return rscript
        
        # 检查常见安装路径
        common_paths = [
            "/usr/bin/Rscript",
            "/usr/local/bin/Rscript",
            "/opt/R/bin/Rscript",
            "/Program Files/R/bin/Rscript.exe",
            "C:\\Program Files\\R\\bin\\Rscript.exe",
            os.path.expanduser("~/.local/bin/Rscript"),
        ]
        
        for path in common_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path
        
        # 如果都找不到，返回默认值（可能在 PATH 中）
        return "Rscript"
    
    def execute_script(self, script_path: str, args: list[str] | None = None) -> subprocess.CompletedProcess:
        """执行 R 脚本"""
        cmd = [self.rscript_path, str(script_path)] + (args or [])
        logger.info(f"Executing R script: {cmd}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5分钟超时
                check=False
            )
            
            if result.returncode != 0:
                error_msg = f"R script failed with return code {result.returncode}: {result.stderr}"
                logger.error(error_msg)
                raise RExecutorError(error_msg)
            
            return result
            
        except FileNotFoundError:
            raise RExecutorError(f"Rscript not found at: {self.rscript_path}")
        except subprocess.TimeoutExpired:
            raise RExecutorError("R script execution timed out after 300 seconds")
        except OSError as e:
            raise RExecutorError(f"Failed to execute R script: {e}")
    
    def execute_code(self, code: str) -> subprocess.CompletedProcess:
        """执行 R 代码"""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_script = os.path.join(temp_dir, "temp_script.R")
            with open(temp_script, 'w') as f:
                f.write(code)
            
            return self.execute_script(temp_script)
    
    def install_package(self, package_name: str) -> subprocess.CompletedProcess:
        """安装 R 包"""
        code = f"if (!requireNamespace('{package_name}', quietly = TRUE)) {{ install.packages('{package_name}') }}"
        return self.execute_code(code)