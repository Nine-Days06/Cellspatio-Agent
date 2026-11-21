"""跨层契约：阶段读取响应的顶层键必须与前端状态类型一致。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TYPES = ROOT / "web" / "src" / "lib" / "types.ts"

CONTRACTS = [
    ("src/api/routes_qc.py", "view", "QcReviewState"),
    ("src/api/routes_pca.py", "view", "PcaState"),
    ("src/api/routes_pca.py", "dimension_view", "PcaDimensionsState"),
    ("src/api/routes_clusters.py", "view", "ClusterState"),
    ("src/api/routes_markers.py", "view", "MarkerState"),
]


def _returns(node: ast.AST) -> list[ast.Return]:
    """收集函数体内 return，跳过嵌套函数/类。"""
    found: list[ast.Return] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Return):
            found.append(child)
            continue
        found.extend(_returns(child))
    return found


def _backend_keys(module: Path, function: str) -> set[str]:
    """解析路由函数唯一的 return 字典字面量顶层字符串键。"""
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    candidates = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function
    ]
    assert len(candidates) == 1, f"{module} 中未找到唯一函数 {function}"
    dicts = [node.value for node in _returns(candidates[0]) if isinstance(node.value, ast.Dict)]
    assert len(dicts) == 1, f"{module}::{function} 应有唯一 return 字典，实际 {len(dicts)}"
    keys: set[str] = set()
    for key in dicts[0].keys:
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
            f"{module}::{function} return 字典含非字符串键"
        )
        keys.add(key.value)
    return keys


def _frontend_fields(name: str) -> tuple[set[str], set[str]]:
    """用正则读取 TS interface 顶层的必需/可选字段。"""
    text = TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name}\b[^{{]*\{{", text)
    assert match, f"{TYPES} 中未找到 export interface {name}"

    depth = 1
    body_start = match.end()
    for body_end in range(body_start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[body_end], 0)
        if depth == 0:
            break
    else:
        pytest.fail(f"{TYPES} 中 interface {name} 缺少闭合花括号")

    required: set[str] = set()
    optional: set[str] = set()
    depth = 0
    for line in text[body_start:body_end].splitlines():
        if depth == 0:
            field = re.match(r"([A-Za-z_$][\w$]*)(\?)?\s*:", line.strip())
            if field:
                (optional if field.group(2) else required).add(field.group(1))
        depth += line.count("{") - line.count("}")
    return required, optional


@pytest.mark.parametrize(("module", "function", "interface"), CONTRACTS)
def test_stage_response_keys_match_frontend_type(
    module: str, function: str, interface: str
) -> None:
    """后端键必须先在前端类型声明；前端必需字段后端必须返回，可选字段不强制。"""
    backend = _backend_keys(ROOT / module, function)
    required, optional = _frontend_fields(interface)
    only_backend = sorted(backend - required - optional)
    missing = sorted(required - backend)
    assert not only_backend and not missing, (
        f"{module}::{function} 与 {interface} 漂移："
        f"仅后端有 {only_backend}；仅前端必需但后端缺失 {missing}"
    )
