"""四个执行路由的阶段目录与失败投影只能来自阶段描述。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[2] / "src" / "api"
FILES = (
    ROUTES / "routes_qc.py",
    ROUTES / "routes_pca.py",
    ROUTES / "routes_clusters.py",
    ROUTES / "routes_markers.py",
)


def test_routes_use_stage_directory() -> None:
    """产物目录不得再由路由手写拼接。"""
    pattern = re.compile(r'root\.parent\s*/\s*["\']sc_')
    hits = [
        f"{path.name}:{lineno}: {line.strip()}"
        for path in FILES
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not hits, "路由仍手写阶段目录：\n" + "\n".join(hits)


def test_routes_use_stage_failure() -> None:
    """失败结果不得再手写技术失败 + 未复核字典。"""
    hits = []
    for path in FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Dict):
                continue
            values = {
                key.value: value.value
                for key, value in zip(node.keys, node.values, strict=True)
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
            }
            if values.get("status") == "failed" and "scientific_status" in values:
                hits.append(f"{path.name}:{node.lineno}")
    assert not hits, "路由仍手写失败投影：" + ", ".join(hits)
