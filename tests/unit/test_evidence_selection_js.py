"""证据选择纯模型：乱序、失败、clear 与重复选择。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不在 PATH")
def test_evidence_selection_js() -> None:
    """用 node 断言选择状态，不启动浏览器或网络。"""
    script = Path(__file__).with_name("evidence_selection_test.mjs")
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
