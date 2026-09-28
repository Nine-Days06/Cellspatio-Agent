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


SUMMARY_PROMPT = (
    "你是会话记忆压缩器。将给定的对话历史压缩为结构化中文摘要，"
    "保留足以让 AI 无缝续接任务的事实，剔除冗余与寒暄。\n"
    "严格按以下 Markdown 结构输出，不要输出结构之外的说明文字：\n"
    "## 目标\n## 关键约束与决策\n### 已完成\n### 进行中\n### 阻塞\n"
    "## 下一步\n## 相关文件与命令"
)


def summarize(llm_client, model: str, prior: str | None,
              messages: list[dict]) -> str:
    """调用 LLM 生成增量摘要；结果为空时抛 ValueError（交由 maybe_compact 降级）。"""
    conversation = "\n".join(
        f"[{msg.get('role')}] {msg.get('content')}" for msg in messages
    )
    user_content = (
        f"<prior-summary>\n{prior or '（无）'}\n</prior-summary>\n\n"
        f"<conversation>\n{conversation}\n</conversation>"
    )
    response = llm_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=2048,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("摘要结果为空")
    return text


def maybe_compact(summary: str | None, offset: int, prepared: list[dict],
                  llm_summarize, system: str | None = None
                  ) -> tuple[str | None, int]:
    """超硬阈值时摘要压缩；任何失败降级为硬截断，绝不抛异常。

    Args:
        summary: 当前累积摘要（无则 None）
        offset:  prepared 中已被摘要消化的前缀长度
        prepared: prepare_history 的产物（全量历史）
        llm_summarize: callable(prior, older_messages) -> str；None 表示跳过压缩
        system: system prompt（None 时读真实 SYSTEM_PROMPT）

    Returns:
        (新摘要, 新 offset)
    """
    window = prepared[offset:]
    if not should_compact(summary, window, system=system):
        return summary, offset
    if llm_summarize is None:
        return summary, offset
    older, _recent = select_recent(window)
    if not older:
        return summary, offset
    try:
        new_summary = llm_summarize(summary, older)
    except Exception as exc:  # noqa: BLE001 降级回退，绝不阻断本轮回答
        logger.warning("会话摘要失败，降级为硬截断：%s", exc)
        return summary, _hard_trim(summary, offset, prepared, system)
    if not new_summary or not str(new_summary).strip():
        logger.warning("会话摘要结果为空，降级为硬截断")
        return summary, _hard_trim(summary, offset, prepared, system)
    return str(new_summary).strip(), offset + len(older)


def _hard_trim(summary: str | None, offset: int, prepared: list[dict],
               system: str | None = None) -> int:
    """无 LLM 时丢弃最旧历史直至低于阈值，至少保留最后 1 条。"""
    while (offset < len(prepared) - 1
           and should_compact(summary, prepared[offset:], system=system)):
        offset += 1
    return offset
