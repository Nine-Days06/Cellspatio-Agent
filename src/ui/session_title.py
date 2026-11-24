"""首轮问答 → 会话标题。

只依赖会话存储与 LLM 客户端：不感知 HTTP、SSE 或工作区。
任何失败都只记 warning 并保留兜底标题，绝不向上抛。
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from src.ui.session_store import OVERWRITABLE_TITLE_SOURCES, SessionStore

logger = logging.getLogger(__name__)

TITLE_PROMPT = (
    "你是会话标题生成器。根据这一轮问答生成一个简短、可辨识的会话标题。\n"
    "要求：只输出标题本身，不要解释、不要加引号或书名号、不要“标题：”之类前缀；\n"
    "与提问使用相同语言；中文不超过 20 字，英文不超过 40 字符；结尾不加句号。"
)

INPUT_MAX_CHARS = 1_000
TITLE_MAX_CHARS = 24
MAX_TOKENS = 64
TIMEOUT_SECONDS = 10.0
FIRST_TURN_MESSAGES = 2

_WRAPPING = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’", "《": "》", "「": "」"}
_TRAILING = "。．.，,；;：:！!？?、 "
_WHITESPACE = re.compile(r"\s+")


def clean_title(raw: str) -> str:
    """去空白/换行、去包裹引号书名号、去结尾标点，截断到上限。"""
    title = _WHITESPACE.sub(" ", str(raw or "")).strip()
    title = re.sub(r"^标题[:：]\s*", "", title)
    # 交替剥离结尾标点与成对包裹符，处理 `标题`. 这类组合
    while True:
        stripped = title.rstrip(_TRAILING)
        if (
            len(stripped) > 1
            and stripped[0] in _WRAPPING
            and stripped[-1] == _WRAPPING[stripped[0]]
        ):
            stripped = stripped[1:-1].strip()
        if stripped == title:
            break
        title = stripped
    return title[:TITLE_MAX_CHARS].strip()


def _request_title(llm_client: Any, model: str, question: str, answer: str) -> str:
    """带硬超时调用模型：超时抛 TimeoutError，由上层记 warning 后保留原标题。"""
    payload = f"<question>\n{question[:INPUT_MAX_CHARS]}\n</question>\n\n<answer>\n{answer[:INPUT_MAX_CHARS]}\n</answer>"

    def call() -> str:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": TITLE_PROMPT},
                {"role": "user", "content": payload},
            ],
            temperature=0,
            max_tokens=MAX_TOKENS,
        )
        return str(response.choices[0].message.content or "")

    # 不能写成 `with ThreadPoolExecutor(...)`：退出上下文会 shutdown(wait=True)，
    # 超时就退化成无限等待。这里显式 wait=False，保证超时后收尾立刻继续。
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-title")
    try:
        future = pool.submit(call)
        try:
            return future.result(timeout=TIMEOUT_SECONDS)
        except FutureTimeout as exc:
            raise TimeoutError(f"标题生成超时（{TIMEOUT_SECONDS}s）") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def maybe_name_session(
    store: SessionStore, session_id: str, llm_client: Any, model: str
) -> str | None:
    """首轮问答完成后按需生成标题；返回写入的标题，未触发或失败返回 None。"""
    if llm_client is None:
        return None
    try:
        session = store.get_session(session_id)
        if session is None or session.get("title_source") not in OVERWRITABLE_TITLE_SOURCES:
            return None
        messages = store.get_messages(session_id)
        if len(messages) != FIRST_TURN_MESSAGES:
            return None
        question = next((m["content"] for m in messages if m["role"] == "user"), "")
        answer = next((m["content"] for m in messages if m["role"] == "assistant"), "")
        if not question or not answer:
            return None
        title = clean_title(_request_title(llm_client, model, question, answer))
        if not title:
            logger.warning("自动命名返回空标题，保留原标题：session=%s", session_id)
            return None
        if not store.set_session_title(session_id, title, source="model"):
            return None
        return title
    except Exception:  # 错误边界：LLM 供应商异常类型不可穷举，命名失败绝不能打断本轮
        logger.warning("自动命名失败，保留原标题：session=%s", session_id, exc_info=True)
        return None
