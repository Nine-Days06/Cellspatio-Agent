"""跨层契约：阶段读取响应（含嵌套对象）的键必须与前端状态类型一致。"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TYPES = ROOT / "web" / "src" / "lib" / "types.ts"

CONTRACTS = [
    ("src/api/routes_qc.py", "view", "QcReviewState"),
    ("src/api/routes_pca.py", "view", "PcaState"),
    ("src/api/routes_pca.py", "dimension_view", "PcaDimensionsState"),
    ("src/api/routes_clusters.py", "view", "ClusterState"),
    ("src/api/routes_markers.py", "view", "MarkerState"),
    ("src/api/routes_markers.py", "candidates", "MarkerPage"),
]

# 边界：以下类型无法静态解析，递归到此跳过，只保留外层键校验。
# - Record<...> / 索引签名（后端运行时字典，如 protocol、artifacts）
# - 多分支联合、交叉类型（Omit<...> & {...}）
# - 外部类型与索引访问（FilterAttempt['status']）
# - 后端非字面量表达式（run 快照、next(...)、动态 .items() 且无键过滤器）
# - 不可解析的 `**row` 展开（如 dimension history 直接展开运行时记录）
Shape = dict[str, Any] | list[Any] | None


REVIEW_BASE = {
    "attempt_id": None,
    "review_id": None,
    "binding_sha256": None,
    "status": None,
    "created_at": None,
    "finished_at": None,
    "source_revision": None,
    "claim_revision": None,
}

# contract 期望独立于实现字段表，避免实现缩水时测试同步缩水。
HISTORY_SHAPES: dict[str, dict[str, Shape]] = {
    "filter": {
        "attempt_id": None,
        "preview_id": None,
        "status": None,
        "directory": None,
        "created_at": None,
        "finished_at": None,
        "result": {
            "cells": None,
            "features": None,
            "error": None,
            "versions": None,
            "artifacts": None,
            "scientific_status": None,
        },
    },
    "pca": {
        "attempt_id": None,
        "binding_sha256": None,
        "status": None,
        "created_at": None,
        "finished_at": None,
        "result": {
            "cells": None,
            "features": None,
            "selected_features": None,
            "usable_features": None,
            "actual_pcs": None,
            "pc_reduction_reason": None,
            "versions": None,
            "error": None,
            "elapsed_seconds": None,
            "scientific_status": None,
        },
    },
    "cluster": {
        **REVIEW_BASE,
        "dims": None,
        "invalidated_reason": None,
        "result": {
            "cells": None,
            "clusters": None,
            "cluster_sizes": None,
            "n_epochs": None,
            "versions": None,
            "seurat_threads": None,
        },
        "error": None,
    },
    "marker": {
        **REVIEW_BASE,
        "invalidated_reason": None,
        "result": {
            "cells": None,
            "features": None,
            "clusters": None,
            "marker_rows": None,
            "positive_rows": None,
            "negative_rows": None,
            "cluster_summary": None,
            "plot_features": None,
            "backend": None,
            "versions": None,
        },
        "error": None,
        "review": None,
    },
}


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


def _const_keys(
    node: ast.AST | None, constants: dict[str, list[str]] | None = None
) -> list[str] | None:
    """元组/列表字面量（或常量列表切片）中的字符串键；其它表达式返回 None。"""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        base = (constants or {}).get(node.value.id)
        index = _slice_index(node.slice)
        if base is not None and index is not None:
            return base[index]
        return None
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    keys: list[str] = []
    for item in node.elts:
        if not (isinstance(item, ast.Constant) and isinstance(item.value, str)):
            return None
        keys.append(item.value)
    return keys


def _slice_index(node: ast.AST) -> slice | int | None:
    """解析列表切片或整数下标中的常量边界。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if not isinstance(node, ast.Slice):
        return None
    bounds: list[int | None] = []
    for part in (node.lower, node.upper, node.step):
        if part is None:
            bounds.append(None)
        elif isinstance(part, ast.Constant) and isinstance(part.value, int):
            bounds.append(part.value)
        else:
            return None
    return slice(*bounds)


def _string_lists(path: Path, names: tuple[str, ...]) -> dict[str, list[str]]:
    """读取模块顶层字符串列表常量，供字典推导式切片解析。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        keys = _const_keys(node.value)
        if keys is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = keys
    return found


def _dictcomp_keys(
    node: ast.DictComp, constants: dict[str, list[str]] | None = None
) -> list[str] | None:
    """解析 `{k: v for k in (...)}` 或 `.items()` 后按元组过滤的键集合。"""
    for gen in node.generators:
        keys = _const_keys(gen.iter, constants)
        if keys is not None:
            return keys
        if isinstance(gen.iter, ast.Call) and isinstance(gen.iter.func, ast.Attribute):
            if gen.iter.func.attr != "items":
                continue
            for check in gen.ifs:
                if (
                    isinstance(check, ast.Compare)
                    and len(check.ops) == 1
                    and isinstance(check.ops[0], ast.In)
                ):
                    keys = _const_keys(check.comparators[0], constants)
                    if keys is not None:
                        return keys
    return None


class _BackendShape:
    """从路由函数 AST 递归提取 return 字典的结构键。"""

    def __init__(self, tree: ast.Module, constants: dict[str, list[str]] | None = None) -> None:
        self.functions = {
            node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.constants = constants or {}

    def shape(self, function: str) -> dict[str, Shape]:
        fn = self.functions.get(function)
        assert fn is not None, f"未找到函数 {function}"
        return self._function_dict(fn)

    def _function_dict(self, fn: ast.FunctionDef) -> dict[str, Shape]:
        dicts = [node.value for node in _returns(fn) if isinstance(node.value, ast.Dict)]
        assert len(dicts) == 1, f"{fn.name} 应有唯一 return 字典，实际 {len(dicts)}"
        shape = self._dict(dicts[0], fn, set())
        assert shape is not None, f"{fn.name} return 字典含无法静态解析的 ** 展开"
        return shape

    def _assignments(
        self, fn: ast.FunctionDef
    ) -> tuple[dict[str, list[Any]], dict[str, list[Any]]]:
        """收集函数内赋值与 `<name>.append(...)`，跳过嵌套函数。"""
        assigned: dict[str, list[Any]] = {}
        appended: dict[str, list[Any]] = {}

        def walk(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            assigned.setdefault(target.id, []).append(child.value)
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    if child.value is not None:
                        assigned.setdefault(child.target.id, []).append(child.value)
                elif (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "append"
                    and isinstance(child.func.value, ast.Name)
                ):
                    appended.setdefault(child.func.value.id, []).extend(child.args)
                walk(child)

        walk(fn)
        return assigned, appended

    def _shape(self, node: ast.AST | None, fn: ast.FunctionDef, seen: set[str]) -> Shape:
        if node is None or (isinstance(node, ast.Constant) and node.value is None):
            return None
        if isinstance(node, ast.Dict):
            return self._dict(node, fn, seen)
        if isinstance(node, ast.DictComp):
            keys = _dictcomp_keys(node, self.constants)
            return None if keys is None else {key: None for key in keys}
        if isinstance(node, ast.List):
            elements = [self._shape(item, fn, seen) for item in node.elts]
            return [self._merge(elements)] if elements else [None]
        if isinstance(node, ast.ListComp):
            return [self._shape(node.elt, fn, seen)]
        if isinstance(node, ast.IfExp):
            body = self._shape(node.body, fn, seen)
            other = self._shape(node.orelse, fn, seen)
            if body is None:
                return other
            if other is None:
                return body
            return self._merge([body, other])
        if isinstance(node, ast.Name):
            return self._name(node.id, fn, seen)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "history_entry" and node.args:
                stage = node.args[0]
                if isinstance(stage, ast.Constant) and isinstance(stage.value, str):
                    return HISTORY_SHAPES.get(stage.value)
            helper = self.functions.get(node.func.id)
            if helper is not None and node.func.id not in seen:
                seen = seen | {node.func.id}
                dicts = [ret.value for ret in _returns(helper) if isinstance(ret.value, ast.Dict)]
                if len(dicts) == 1:
                    return self._dict(dicts[0], helper, seen)
        return None

    def _name(self, name: str, fn: ast.FunctionDef, seen: set[str]) -> Shape:
        if name in seen:
            return None
        assigned, appended = self._assignments(fn)
        if name in appended:
            return [self._merge([self._shape(item, fn, seen | {name}) for item in appended[name]])]
        for value in assigned.get(name, []):
            shape = self._shape(value, fn, seen | {name})
            if shape is not None:
                return shape
        return None

    def _dict(self, node: ast.Dict, fn: ast.FunctionDef, seen: set[str]) -> dict[str, Shape] | None:
        fields: dict[str, Shape] = {}
        for key, value in zip(node.keys, node.values, strict=False):
            if key is None:
                unpacked = self._shape(value, fn, seen)
                if not isinstance(unpacked, dict):
                    return None
                fields.update(unpacked)
                continue
            assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
                f"{fn.name} return 字典含非字符串键"
            )
            fields[key.value] = self._shape(value, fn, seen)
        return fields

    @staticmethod
    def _merge(shapes: list[Shape]) -> Shape:
        objects = [shape for shape in shapes if isinstance(shape, dict)]
        if not objects:
            return None
        merged: dict[str, Shape] = {}
        for obj in objects:
            for key, value in obj.items():
                if key not in merged:
                    merged[key] = value
        return merged


class _FrontendShape:
    """从 types.ts 递归解析 interface / 内联对象 / 数组元素。"""

    def __init__(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"//[^\n]*", "", text)
        self.text = text
        self._cache: dict[str, dict[str, tuple[bool, Any]]] = {}

    def interface(self, name: str) -> dict[str, tuple[bool, Any]]:
        if name in self._cache:
            return self._cache[name]
        match = re.search(rf"export interface {name}\b[^{{;]*\{{", self.text)
        assert match, f"{TYPES} 中未找到 export interface {name}"
        body = self._block(match.end() - 1)
        fields = self._fields(body)
        self._cache[name] = fields
        return fields

    def _block(self, start: int) -> str:
        """返回从 `{` 开始、匹配到对应 `}` 的内部文本。"""
        depth = 0
        for index in range(start, len(self.text)):
            char = self.text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return self.text[start + 1 : index]
        pytest.fail(f"{TYPES} 中存在未闭合的花括号")

    def _fields(self, body: str) -> dict[str, tuple[bool, Any]]:
        pattern = re.compile(r"(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_$][\w$]*)(\?)?)\s*:")
        fields: dict[str, tuple[bool, Any]] = {}
        index = 0
        while index < len(body):
            while index < len(body) and body[index] in " \t\r\n;,":
                index += 1
            match = pattern.match(body, index)
            if not match:
                index += 1
                continue
            name = match.group(1) or match.group(2) or match.group(3)
            optional = bool(match.group(4))
            end = self._type_end(body, match.end())
            fields[name] = (optional, self._type(body[match.end() : end].strip()))
            index = end
        return fields

    @staticmethod
    def _type_end(body: str, start: int) -> int:
        depths = {"{": 0, "(": 0, "[": 0, "<": 0}
        pairs = {"}": "{", ")": "(", "]": "[", ">": "<"}
        quote = ""
        for index in range(start, len(body)):
            char = body[index]
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in "'\"`":
                quote = char
            elif char in depths:
                depths[char] += 1
            elif char in pairs:
                depths[pairs[char]] = max(0, depths[pairs[char]] - 1)
            elif char in ";\n" and not any(depths.values()):
                return index
        return len(body)

    def _type(self, text: str) -> Any:
        text = text.strip()
        parts = self._split(text, "|")
        if len(parts) > 1:
            parts = [part for part in parts if part.strip() not in ("null", "undefined")]
            if len(parts) != 1:
                return None
            text = parts[0].strip()
        if len(self._split(text, "&")) > 1:
            return None
        if text.endswith("[]"):
            return ("array", self._type(text[:-2]))
        array = re.fullmatch(r"Array<(.+)>", text)
        if array:
            return ("array", self._type(array.group(1)))
        if text.startswith("{") and text.endswith("}"):
            return self._fields(text[1:-1])
        if re.fullmatch(r"[A-Za-z_$][\w$]*", text) and text in re.findall(
            r"export interface (\w+)", self.text
        ):
            return self.interface(text)
        return None

    @staticmethod
    def _split(text: str, sep: str) -> list[str]:
        parts: list[str] = []
        depth = 0
        quote = ""
        last = 0
        for index, char in enumerate(text):
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in "'\"`":
                quote = char
            elif char in "{([<":
                depth += 1
            elif char in "})]>":
                depth -= 1
            elif char == sep and depth == 0:
                parts.append(text[last:index])
                last = index + 1
        parts.append(text[last:])
        return parts


def _compare(path: str, backend: Shape, frontend: Any, drift: list[str]) -> None:
    """只比较结构键；可选字段由前端 `?` 决定是否必须。"""
    if backend is None or frontend is None:
        return
    if isinstance(frontend, tuple):
        if isinstance(backend, list):
            _compare(f"{path}[]", backend[0], frontend[1], drift)
        return
    if not isinstance(backend, dict) or not isinstance(frontend, dict):
        return
    required = {name for name, (optional, _) in frontend.items() if not optional}
    declared = set(frontend)
    drift.extend(f"{path}.{key}" for key in sorted(set(backend) - declared))
    drift.extend(f"{path}.{key}" for key in sorted(required - set(backend)))
    for key in sorted(set(backend) & declared):
        _compare(f"{path}.{key}", backend[key], frontend[key][1], drift)


BACKEND_CONSTANTS = _string_lists(ROOT / "src" / "analysis" / "sc_markers.py", ("MARKER_FIELDS",))


@pytest.mark.parametrize(("module", "function", "interface"), CONTRACTS)
def test_stage_response_keys_match_frontend_type(
    module: str, function: str, interface: str
) -> None:
    """后端键必须先在前端类型声明；前端必需字段后端必须返回，可选字段不强制。"""
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"), filename=str(ROOT / module))
    backend = _BackendShape(tree, BACKEND_CONSTANTS).shape(function)
    frontend = _FrontendShape(TYPES).interface(interface)

    drift: list[str] = []
    _compare(interface, backend, frontend, drift)
    assert not drift, f"{module}::{function} 与 {interface} 漂移：\n" + "\n".join(drift)


@pytest.mark.parametrize(
    ("module", "stage"),
    [
        ("src/api/routes_qc.py", "filter"),
        ("src/api/routes_pca.py", "pca"),
        ("src/api/routes_clusters.py", "cluster"),
        ("src/api/routes_markers.py", "marker"),
    ],
)
def test_view_attempts_use_stage_history_shape(module: str, stage: str) -> None:
    """四个视图的 attempts 必须保留 history_entry 的嵌套响应形状。"""
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"), filename=str(ROOT / module))
    backend = _BackendShape(tree, BACKEND_CONSTANTS).shape("view")

    assert backend["attempts"] == [HISTORY_SHAPES[stage]]
