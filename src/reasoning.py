"""流式思考字段提取。

推理型供应商在流式 delta 上附加思考内容，常见字段名并不统一。这里只读取
已知字段且只接受非空字符串；缺失、空值或类型不符一律静默忽略，调用方
无需为不同供应商写分支。
"""

from __future__ import annotations

from typing import Any

REASONING_FIELDS = ("reasoning_content", "reasoning")


def _field_value(delta: Any, name: str) -> Any:
    """先取直接属性，再取 pydantic 的 model_extra（SDK 未声明字段时）。"""
    if isinstance(delta, dict):
        return delta.get(name)
    value = getattr(delta, name, None)
    if value is not None:
        return value
    extra = getattr(delta, "model_extra", None)
    if isinstance(extra, dict):
        return extra.get(name)
    return None


def reasoning_delta(delta: Any) -> str:
    """取一个 delta 上的思考文本；没有可用内容时返回空串。"""
    if delta is None:
        return ""
    for name in REASONING_FIELDS:
        value = _field_value(delta, name)
        if isinstance(value, str) and value:
            return value
    return ""


def split_delta(delta: Any) -> tuple[str, str]:
    """拆出一个流式 delta 的思考与正文，按(思考, 正文)返回。"""
    if delta is None:
        return "", ""
    content = _field_value(delta, "content")
    return reasoning_delta(delta), content if isinstance(content, str) and content else ""
