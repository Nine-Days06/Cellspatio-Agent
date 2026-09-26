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
        # 延迟读取：允许在 load_dotenv 之后构造也能拿到 R_HOME
        self._r_home_override = r_home
        self.r_home = r_home or os.getenv("R_HOME", "/usr/lib/R")
        self.rscript_path = self._find_rscript()

    def _resolve_rscript(self) -> str:
        """解析 Rscript：优先用缓存；若仍是占位符则重查（兼容 env 晚加载）"""
        if self.rscript_path and self.rscript_path != "Rscript" and os.path.exists(self.rscript_path):
            return self.rscript_path
        if self._r_home_override is None and os.getenv("R_HOME"):
            self.r_home = os.getenv("R_HOME", "")
        self.rscript_path = self._find_rscript()
        return self.rscript_path

    def _find_rscript(self) -> str:
        """查找 Rscript 可执行文件（跨平台动态检测）"""
        rscript_name = "Rscript.exe" if os.name == "nt" else "Rscript"

        # 1. R_HOME 环境变量（显式指定，优先级最高）
        r_home = self.r_home or os.getenv("R_HOME")
        if r_home:
            candidate = os.path.join(r_home, "bin", rscript_name)
            if os.path.exists(candidate):
                return candidate

        # 2. PATH 中查找（用户安装时勾选 "Add to PATH"）
        rscript = shutil.which("Rscript")
        if rscript:
            return rscript

        # 3. Windows：从注册表读取 R 安装路径
        if os.name == "nt":
            reg_path = self._get_r_home_from_registry()
            if reg_path:
                candidate = os.path.join(reg_path, "bin", rscript_name)
                if os.path.exists(candidate):
                    return candidate

        # 4. 跨平台常见默认路径（兜底）
        common_paths = self._get_common_r_paths()
        for path in common_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path

        # 5. 实在找不到，返回默认命令名（交给 subprocess 报错）
        return rscript_name

    def _get_r_home_from_registry(self) -> str | None:
        """从 Windows 注册表读取 R 安装路径 (HKLM\\Software\\R-core\\R)"""
        try:
            import winreg
            # R 4.x+ 通常写在 HKLM\\Software\\R-core\\R
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\R-core\R") as key:
                install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                if install_path and os.path.isdir(install_path):
                    return install_path
        except (FileNotFoundError, OSError, ImportError):
            pass
        # 兼容旧版/用户级安装：HKCU
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\R-core\R") as key:
                install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                if install_path and os.path.isdir(install_path):
                    return install_path
        except (FileNotFoundError, OSError, ImportError):
            pass
        return None

    def _get_common_r_paths(self) -> list[str]:
        """跨平台常见默认安装路径（按可能性排序）"""
        rscript_name = "Rscript.exe" if os.name == "nt" else "Rscript"
        paths = []

        if os.name == "nt":
            # Windows: 遍历 Program Files、用户目录、常见自定义盘符
            for base in [
                os.getenv("ProgramFiles", r"C:\Program Files"),
                os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                os.getenv("LOCALAPPDATA", ""),
                "D:\\", "E:\\", "F:\\",
            ]:
                if not base:
                    continue
                # R-4.x.x 版本目录
                for ver in ["R-4.5.3", "R-4.5.2", "R-4.5.1", "R-4.5.0",
                            "R-4.4.3", "R-4.4.2", "R-4.4.1", "R-4.4.0",
                            "R-4.3.3", "R-4.3.2", "R-4.3.1", "R-4.3.0",
                            "R-4.2.3", "R-4.2.2", "R-4.2.1", "R-4.2.0"]:
                    paths.append(os.path.join(base, ver, "bin", rscript_name))
                # 通用 R 目录
                paths.append(os.path.join(base, "R", "bin", rscript_name))
        else:
            # Linux/macOS 常见路径
            paths.extend([
                "/usr/bin/Rscript",
                "/usr/local/bin/Rscript",
                "/opt/R/bin/Rscript",
                "/opt/homebrew/bin/Rscript",  # Apple Silicon Homebrew
                os.path.expanduser("~/.local/bin/Rscript"),
            ])

        return paths
    
    def execute_script(self, script_path: str, args: list[str] | None = None) -> subprocess.CompletedProcess:
        """执行 R 脚本"""
        cmd = [self._resolve_rscript(), str(script_path)] + (args or [])
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