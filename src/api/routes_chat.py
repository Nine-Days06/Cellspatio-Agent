"""POST /api/chat：SSE 流端点。工作线程跑 run_turn，事件经 asyncio 队列回传。"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.chat_service import run_turn, session_lock
from src.api.events import make_event, sse_frame
from src.ui.session_store import SessionStore

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


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    """单请求即一个完整 SSE 流：事件顺序为 delta/tool_status/confirm_card/chart → done。"""
    agent = request.app.state.agent
    store = SessionStore()
    if store.get_session(body.session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    lock = session_lock(body.session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话已有任务在进行")

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

        def worker() -> None:
            try:
                try:
                    run_turn(agent, store, body.session_id, body.prompt, emit)
                except Exception as exc:  # 任何失败都要变成 error 事件
                    logger.exception("chat run_turn failed")  # except 块内自动带 traceback
                    emit(make_event("error", message=str(exc) or type(exc).__name__,
                                    retryable=True))
            finally:
                lock.release()
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                except RuntimeError:  # pragma: no cover - loop 已关闭，线程收尾即可
                    pass

        threading.Thread(target=worker, name="chat-worker", daemon=True).start()
    except Exception:  # 起流前失败必须先还锁，否则会话永久 409
        lock.release()
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
