"""上下文记忆压缩：token 估算、历史裁剪、增量摘要与降级回退。

预算（1 token ≈ 4 字符）：
- CONTEXT_LIMIT  模型上下文上限
- OUTPUT_RESERVE 预留输出空间
- SUMMARY_RESERVE 预留摘要空间
- TRIGGER        超过则尝试 LLM 摘要压缩（硬阈值）
- SOFT_TRIGGER   逼近时 UI 软提醒，不阻断
- RECENT_TOKEN_BUDGET 压缩后保留在窗口内的最近消息预算
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CONTEXT_LIMIT = 128_000
OUTPUT_RESERVE = 8_000
SUMMARY_RESERVE = 2_000
TRIGGER = CONTEXT_LIMIT - OUTPUT_RESERVE - SUMMARY_RESERVE  # 118_000
SOFT_TRIGGER = 96_000
RECENT_TOKEN_BUDGET = 12_000
TOOL_OUTPUT_MAX_CHARS = 2_000
TRUNCATION_NOTE = "\n…（已截断）"


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（1 token ≈ 4 字符）。"""
    return len(text) // 4


def prepare_history(messages: list[dict]) -> list[dict]:
    """裁剪超长单条消息内容，返回等值副本（不改原对象）。"""
    prepared = []
    for msg in messages:
        content = str(msg.get("content") or "")
        if len(content) > TOOL_OUTPUT_MAX_CHARS:
            content = content[:TOOL_OUTPUT_MAX_CHARS] + TRUNCATION_NOTE
        prepared.append({**msg, "content": content})
    return prepared


def context_tokens(summary: str | None, window: list[dict],
                   system: str | None = None) -> int:
    """system + 摘要 + 历史窗口的 token 总量。"""
    if system is None:
        from src.control.tools import SYSTEM_PROMPT

        system = SYSTEM_PROMPT
    total = estimate_tokens(system or "")
    total += estimate_tokens(summary or "")
    for msg in window:
        total += estimate_tokens(str(msg.get("content") or ""))
    return total


def should_compact(summary: str | None, window: list[dict],
                   system: str | None = None) -> bool:
    """是否达到 LLM 摘要压缩的硬阈值（TRIGGER 为模块全局，便于测试 patch）。"""
    return context_tokens(summary, window, system=system) > TRIGGER


def select_recent(window: list[dict], budget: int | None = None
                  ) -> tuple[list[dict], list[dict]]:
    """从窗口尾部向前取预算内的最近消息，返回 (older, recent)。

    至少保留 1 条最近消息，即使单条已超预算。
    """
    if budget is None:
        budget = RECENT_TOKEN_BUDGET
    if not window:
        return [], []
    total = 0
    split = len(window)
    for i in range(len(window) - 1, -1, -1):
        cost = estimate_tokens(str(window[i].get("content") or ""))
        if total + cost > budget and total > 0:
            split = i + 1
            break
        total += cost
        split = i
    return window[:split], window[split:]
