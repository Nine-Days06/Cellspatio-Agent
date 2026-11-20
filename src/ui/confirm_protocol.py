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
