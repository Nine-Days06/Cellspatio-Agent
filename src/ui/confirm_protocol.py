"""确认协议常量：不依赖第三方库，供存储层与状态机共用。"""

from __future__ import annotations

from typing import Any

ACTION_SCRIPT_CONFIRM = "script_confirm"
ACTION_SCRIPT_CANCEL = "script_cancel"
ACTION_DATA_CONFIRM = "data_confirm"
SCRIPT_ACTIONS = frozenset({ACTION_SCRIPT_CONFIRM, ACTION_SCRIPT_CANCEL})

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

SCRIPT_STATUS_BY_ACTION = {
    ACTION_SCRIPT_CONFIRM: STATUS_CONFIRMED,
    ACTION_SCRIPT_CANCEL: STATUS_CANCELLED,
    ACTION_DATA_CONFIRM: None,
}

PendingScript = dict[str, Any]


def script_binding(
    analysis_type: str | None,
    params: dict[str, Any],
    script: str,
    method_context: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """准备保存、刷新恢复与确认/取消核验使用同一组脚本绑定事实。"""
    return {
        "session_id": session_id,
        "analysis_type": analysis_type,
        "script": script,
        "params": params,
        "method_context": method_context,
    }
