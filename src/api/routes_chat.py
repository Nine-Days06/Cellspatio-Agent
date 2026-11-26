"""POST /api/chat：SSE 流端点。工作线程跑 run_turn，事件经 asyncio 队列回传。"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import ExitStack
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StrictBool

from src.api.chat_service import BeforeDone, persist_message, run_turn
from src.api.deps import StoreDep
from src.api.events import make_event, sse_frame
from src.api.session_locks import session_guard
from src.ui.session_store import SessionStore
from src.ui.session_title import maybe_name_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatRequest(BaseModel):
    """POST /api/chat 请求体。"""

    session_id: str
    prompt: str
    rerank: StrictBool = False


@router.post("/chat")
async def chat(body: ChatRequest, request: Request, store: StoreDep) -> StreamingResponse:
    """单请求即一个完整 SSE 流：事件顺序为 delta/tool_status/confirm_card/chart → done。"""
    agent = request.app.state.agent
    if store.get_session(body.session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 锁要跨 worker 生命周期，不能用 with 包住路由本体的返回。
    guard = ExitStack()
    guard.enter_context(session_guard(body.session_id))

    try:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        abort = threading.Event()

        def emit(event: dict[str, Any]) -> bool:
            """工作线程侧发射。已中止或事件循环已关时返回 False（协作式取消信号）。"""
            if abort.is_set():
                return False
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # pragma: no cover - loop 已关闭
                return False
            return True

        partial: list[str] = []

        def emit_recording(event: dict[str, Any]) -> bool:
            """记录已生成的增量文本，失败时才能把部分回答落库。"""
            if (
                event.get("type") == "delta"
                and event.get("source") != "reasoning"
                and isinstance(event.get("text"), str)
            ):
                partial.append(event["text"])
            return emit(event)

        def worker() -> None:
            try:
                try:
                    run_turn(
                        agent,
                        store,
                        body.session_id,
                        body.prompt,
                        emit_recording,
                        before_done=_auto_name(agent),
                        rerank=body.rerank,
                    )
                except Exception as exc:  # 任何失败都要变成 error 事件
                    logger.exception("chat run_turn failed")  # except 块内自动带 traceback
                    # 已生成的文本不能只留在前端草稿里：断线后用户刷新就永久丢失。
                    if partial:
                        try:
                            persist_message(store, body.session_id, "".join(partial))
                        except Exception:  # pragma: no cover - 落库失败不掩盖原始错误
                            logger.exception("failed to persist partial chat answer")
                    emit(
                        make_event("error", message=str(exc) or type(exc).__name__, retryable=True)
                    )
            finally:
                guard.close()
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                except RuntimeError:  # pragma: no cover - loop 已关闭，线程收尾即可
                    pass

        threading.Thread(target=worker, name="chat-worker", daemon=True).start()
    except Exception:  # 起流前失败必须先还锁，否则会话永久 409
        guard.close()
        raise

    async def event_stream() -> AsyncIterator[str]:
        try:
            while True:
                event = await queue.get()
                if event is None:  # 哨兵：worker 收尾
                    break
                yield sse_frame(event)
        finally:
            # 客户端断开时生成器被 close：置位 abort 让 emit 返 False，
            # AgentRuntime 在下一个检查点协作式退出
            abort.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _auto_name(agent: Any) -> BeforeDone:
    """首轮问答完成后的自动命名挂点；无 LLM 客户端时由命名模块自行跳过。"""

    def hook(store: SessionStore, session_id: str) -> None:
        maybe_name_session(
            store,
            session_id,
            getattr(agent, "llm_client", None),
            getattr(agent, "llm_model", "") or "",
        )

    return hook
