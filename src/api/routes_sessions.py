"""会话 CRUD、消息恢复端点：SessionStore 逐请求短连接。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.api.chat_service import session_lock, soft_warn_for
from src.api.deps import StoreDep
from src.api.events import wire_message
from src.ui.confirm_flow import (
    ERR_MISSING_SOURCE,
    ERR_NO_PENDING_SCRIPT,
    ERR_UNKNOWN_SOURCE,
    ConfirmFlowError,
    confirm_data,
    confirm_script,
    restore_pending,
)
from src.ui.confirm_protocol import ACTION_DATA_CONFIRM, SCRIPT_ACTIONS
from src.ui.session_store import SessionStore
from src.ui.usage import session_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])

_CONFIRM_ERROR_STATUS = {
    ERR_NO_PENDING_SCRIPT: 409,
    ERR_MISSING_SOURCE: 400,
    ERR_UNKNOWN_SOURCE: 400,
}


def _require_session(store: SessionStore, session_id: str) -> dict[str, Any]:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


class CreateSessionRequest(BaseModel):
    """POST /api/sessions 请求体：title 缺省时由 store 生成时间戳标题。"""

    title: str | None = None
    project_id: str | None = None


@router.get("/sessions")
def list_sessions(store: StoreDep, include_archived: bool = False) -> dict[str, Any]:
    """列出会话（可选含归档）；只读，不加会话锁。"""
    return {"sessions": store.list_sessions(include_archived=include_archived)}


@router.post("/sessions")
def create_session(store: StoreDep, body: CreateSessionRequest | None = None) -> dict[str, Any]:
    """新建会话；显式 project_id 时归入该项目，否则保持未归类。"""
    title = body.title if body and body.title else "新会话"
    try:
        session_id = store.create_session(title, body.project_id if body else None)
    except ValueError as exc:
        status = 409 if "归档" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"session": store.get_session(session_id)}


@router.get("/sessions/{session_id}/usage")
def get_session_usage(session_id: str, store: StoreDep) -> dict[str, Any]:
    """返回会话产物、.wrroc 与 snapshots 的只读占用汇总。"""
    try:
        return session_usage(store, session_id, Path.cwd())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class UpdateSessionRequest(BaseModel):
    """PATCH /api/sessions/{id}：project_id 显式传 null 表示移回未归类。"""

    project_id: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: UpdateSessionRequest, store: StoreDep) -> dict[str, Any]:
    """更新归属/置顶/归档；归档前先拿会话锁，避免误藏运行中的对话。"""
    _require_session(store, session_id)
    lock = None
    if body.archived:
        lock = session_lock(session_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="该会话已有任务在进行")
    try:
        if "project_id" in body.model_fields_set:
            store.move_session(session_id, body.project_id)
        if body.pinned is not None:
            store.set_session_pinned(session_id, body.pinned)
        if body.archived is not None:
            store.archive_session(session_id, body.archived)
    except ValueError as exc:
        status = 409 if "归档" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    finally:
        if lock is not None:
            lock.release()
    return {"session": store.get_session(session_id)}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, store: StoreDep) -> dict[str, Any]:
    """删除会话；先取非阻塞会话锁，避免删掉生成中的会话。"""
    _require_session(store, session_id)
    # 生成中删除会让 worker 落库失败 → 与 chat/confirm 同一把非阻塞会话锁
    lock = session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话已有任务在进行")
    try:
        store.delete_session(session_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    finally:
        lock.release()
    return {"deleted": session_id}


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, store: StoreDep, restore: int = 0) -> dict[str, Any]:
    """返回会话与消息；只读，仅 restore=1 时改写待确认脚本并落库。"""
    session = _require_session(store, session_id)
    messages = store.get_messages(session_id)
    return {
        "session": session,
        "messages": [wire_message(m) for m in messages],
        "pending_script": restore_pending(store, session_id, messages) if restore else None,
        "soft_warn": soft_warn_for(store, session_id),
    }


class ConfirmRequest(BaseModel):
    """POST /api/confirm 请求体。action 决定分支，data_confirm 需带三项来源信息。"""

    session_id: str
    action: str
    source: str | None = None
    asset_id: str | None = None
    query: str | None = None


# 用同步 def：FastAPI 丢到线程池执行，避免阻塞事件循环上的其它 SSE 流
@router.post("/confirm", tags=["confirm"])
def confirm(body: ConfirmRequest, request: Request, store: StoreDep) -> dict[str, Any]:
    """HITL 确认端点：脚本确认 / 脚本取消 / 数据下载确认。

    script_regen 不设端点：前端用 pending_script.user_request 直接重发
    /api/chat，与 Streamlit 的「重新生成」不改 DB status 行为一致。
    """
    agent = request.app.state.agent
    _require_session(store, body.session_id)
    lock = session_lock(body.session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话已有任务在进行")
    try:
        if body.action == ACTION_DATA_CONFIRM:
            last = confirm_data(
                agent,
                store,
                body.session_id,
                source=body.source,
                asset_id=body.asset_id,
                query=body.query,
            )
        elif body.action in SCRIPT_ACTIONS:
            last = confirm_script(agent, store, body.session_id, body.action)
        else:
            raise HTTPException(status_code=400, detail=f"未知动作: {body.action}")
        return {"message": wire_message(last)}
    except ConfirmFlowError as exc:
        status = _CONFIRM_ERROR_STATUS.get(exc.code, 502)
        if status == 502:
            logger.exception("confirm action failed: %s", body.action)
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except HTTPException:
        raise  # 已结构化的 4xx 直接透传，勿吞成 502
    except Exception as exc:  # 兜底：任意失败都保持 {"detail":...} JSON 契约
        logger.exception("confirm action failed: %s", body.action)
        raise HTTPException(status_code=502, detail=str(exc) or type(exc).__name__) from exc
    finally:
        lock.release()
