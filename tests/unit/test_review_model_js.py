"""复核页纯模型：角色、阻断文案与服务端动作规格。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不在 PATH")
def test_review_model_js() -> None:
    """用 node 断言纯函数行为，不启动浏览器或网络。"""
    script = Path(__file__).with_name("review_model_test.mjs")
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
