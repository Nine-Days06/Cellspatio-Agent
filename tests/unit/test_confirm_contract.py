"""跨层契约：后端确认动作/状态常量必须与前端字面量类型一致。"""

from __future__ import annotations

import re
from pathlib import Path

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
    return set(re.findall(r"'([^']+)'", match.group(1)))


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
