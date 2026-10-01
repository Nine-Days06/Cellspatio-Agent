"""回归守卫：API 层不得再拉起 streamlit（终态已移除 Streamlit UI）。"""
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _run(code: str) -> subprocess.CompletedProcess:
    # cwd 固定为仓库根，确保子进程 `import src.*` 解析到本仓库
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=_ROOT,
        check=False,
    )


def test_importing_api_app_does_not_import_streamlit():
    """import src.api.app（含全部路由）后 sys.modules 里不得出现 streamlit。"""
    proc = _run("import sys; import src.api.app; assert 'streamlit' not in sys.modules")
    assert proc.returncode == 0, proc.stderr


def test_streamlit_is_not_a_declared_dependency():
    """依赖清单里不得再声明 streamlit。"""
    reqs = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "streamlit" not in reqs.lower()
    assert "streamlit" not in pyproject.lower()
