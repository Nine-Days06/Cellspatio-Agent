"""跨层契约：后端确认动作/状态常量必须与前端字面量类型一致。"""

from __future__ import annotations

import re
from pathlib import Path

from src.ui.confirm_protocol import SCRIPT_STATUS_BY_ACTION

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "src" / "ui" / "confirm_protocol.py"
TYPES = ROOT / "web" / "src" / "lib" / "types.ts"
CONFIRM_STATE = ROOT / "web" / "src" / "lib" / "confirmState.ts"


def _backend_values(prefix: str) -> set[str]:
    text = PROTOCOL.read_text(encoding="utf-8")
    pairs = re.findall(rf'^({prefix}[A-Z0-9_]+)\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert pairs, f"{PROTOCOL} 中未找到 {prefix}* 常量"
    return {value for _, value in pairs}


def _frontend_union(name: str) -> set[str]:
    text = TYPES.read_text(encoding="utf-8")
    match = re.search(rf"export type {name}\s*=\s*([^\n]+)", text)
    assert match, f"{TYPES} 中未找到 export type {name}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _frontend_array(name: str) -> set[str]:
    text = CONFIRM_STATE.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match, f"{CONFIRM_STATE} 中未找到 export const {name}"
    constants = _frontend_constants()
    values = re.findall(r"(?:'([^']+)'|(ACTION_[A-Z_]+|STATUS_[A-Z_]+))", match.group(1))
    return {literal or constants[constant] for literal, constant in values}


def _frontend_constants() -> dict[str, str]:
    text = CONFIRM_STATE.read_text(encoding="utf-8")
    return dict(
        re.findall(
            r"^export const (ACTION_[A-Z_]+|STATUS_[A-Z_]+)\s*=\s*'([^']+)'", text, re.MULTILINE
        )
    )


def _frontend_status_by_action() -> dict[str, str | None]:
    text = CONFIRM_STATE.read_text(encoding="utf-8")
    match = re.search(
        r"export const SCRIPT_STATUS_BY_ACTION\s*=\s*\{(.*?)\}\s*as const", text, re.DOTALL
    )
    assert match, "confirmState.ts 中未找到 SCRIPT_STATUS_BY_ACTION"
    constants = _frontend_constants()
    pairs = re.findall(r"\[(ACTION_[A-Z_]+)\]:\s*(STATUS_[A-Z_]+|null)", match.group(1))
    assert pairs, "SCRIPT_STATUS_BY_ACTION 未解析到映射"
    return {constants[key]: None if value == "null" else constants[value] for key, value in pairs}


def _assert_same(backend: set[str], frontend: set[str], label: str) -> None:
    assert backend == frontend, (
        f"{label}漂移：仅后端有 {sorted(backend - frontend)}，仅前端有 {sorted(frontend - backend)}"
    )


def test_confirm_actions_match_frontend_type():
    backend = _backend_values("ACTION_")
    _assert_same(backend, _frontend_union("ConfirmAction"), "确认动作类型集合")
    _assert_same(backend, _frontend_array("CONFIRM_ACTIONS"), "CONFIRM_ACTIONS 运行时常量")


def test_script_statuses_match_frontend_type():
    backend = _backend_values("STATUS_")
    _assert_same(backend, _frontend_union("PendingScriptStatus"), "脚本状态类型集合")
    _assert_same(backend, _frontend_array("SCRIPT_STATUSES"), "SCRIPT_STATUSES 运行时常量")


def test_script_status_mapping_matches_frontend():
    assert SCRIPT_STATUS_BY_ACTION == _frontend_status_by_action()
