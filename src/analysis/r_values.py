"""把固定协议值序列化为 R 字面量。"""

from __future__ import annotations

import json
from typing import Any


def r_value(value: Any) -> str:
    """只序列化服务端固定协议到 R 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, list):
        return "c(" + ",".join(r_value(item) for item in value) + ")"
    return json.dumps(value)
