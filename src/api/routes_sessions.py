"""会话 CRUD、消息恢复端点：SessionStore 逐请求短连接。"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import nullcontext
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.api.chat_service import soft_warn_for
from src.api.deps import StoreDep
from src.api.errors import store_status
from src.api.events import wire_message
from src.api.session_locks import session_guard
from src.ui.confirm_flow import (
    ERR_MISSING_SOURCE,
    ERR_NO_PENDING_SCRIPT,
    ERR_NOT_SCRIPT_ACTION,
    ERR_UNKNOWN_SOURCE,
    ConfirmFlowError,
    confirm_data,
    confirm_script,
    restore_pending,
)
from src.ui.confirm_protocol import ACTION_DATA_CONFIRM, SCRIPT_ACTIONS
from src.ui.record_delete import delete_records
from src.ui.session_store import SessionStore
from src.ui.store_errors import StoreError
from src.ui.usage import session_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])

_CONFIRM_ERROR_STATUS = {
    ERR_NO_PENDING_SCRIPT: 409,
    ERR_MISSING_SOURCE: 400,
    ERR_UNKNOWN_SOURCE: 400,
    ERR_NOT_SCRIPT_ACTION: 400,
}


def _require_session(store: SessionStore, session_id: str) -> dict[str, Any]:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


class CreateSessionRequest(BaseModel):
    """POST /api/sessions 请求体：title 缺省时记 ``default``（“新会话”），传值记 ``explicit``。"""

    title: str | None = None
    project_id: str | None = None


@router.get("/sessions")
def list_sessions(store: StoreDep, include_archived: bool = False) -> dict[str, Any]:
    """列出会话（可选含归档）；只读，不加会话锁。"""
    return {"sessions": store.list_sessions(include_archived=include_archived)}


@router.post("/sessions")
def create_session(store: StoreDep, body: CreateSessionRequest | None = None) -> dict[str, Any]:
    """新建会话；显式 project_id 时归入该项目，否则保持未归类。

    未传标题时传 None 给存储层，由存储层记为 ``default`` 以便首条消息覆盖；
    传了标题则记为 ``explicit``，不再被自动命名改写。
    """
    title = body.title if body and body.title else None
    try:
        session_id = store.create_session(title, body.project_id if body else None)
    except StoreError as exc:
        raise HTTPException(status_code=store_status(exc), detail=str(exc)) from exc
    return {"session": store.get_session(session_id)}


@router.get("/sessions/{session_id}/usage")
def get_session_usage(session_id: str, request: Request, store: StoreDep) -> dict[str, Any]:
    """返回会话产物、.wrroc 与 snapshots 的只读占用汇总。"""
    try:
        return session_usage(store, session_id, request.app.state.repo_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class UpdateSessionRequest(BaseModel):
    """PATCH /api/sessions/{id}：project_id 显式传 null 表示移回未归类；title 非空时写 ``explicit``。"""

    project_id: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    title: str | None = None


@router.patch("/sessions/{session_id}")
def update_session(session_id: str, body: UpdateSessionRequest, store: StoreDep) -> dict[str, Any]:
    """更新标题/归属/置顶/归档；归档前先拿会话锁，避免误藏运行中的对话。"""
    _require_session(store, session_id)
    guard = session_guard(session_id) if body.archived else nullcontext()
    with guard:
        try:
            if body.title is not None:
                store.set_session_title(session_id, body.title, source="explicit")
            if "project_id" in body.model_fields_set:
                store.move_session(session_id, body.project_id)
            if body.pinned is not None:
                store.set_session_pinned(session_id, body.pinned)
            if body.archived is not None:
                store.archive_session(session_id, body.archived)
        except StoreError as exc:
            raise HTTPException(status_code=store_status(exc), detail=str(exc)) from exc
    return {"session": store.get_session(session_id)}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request, store: StoreDep) -> dict[str, Any]:
    """删除会话与磁盘产物；复用 CLI 的“先暂存再删库”实现。"""
    _require_session(store, session_id)
    # 生成中删除会让 worker 落库失败 → 与 chat/confirm 同一把非阻塞会话锁
    with session_guard(session_id):
        try:
            result = delete_records(
                "session", session_id, store=store, root=request.app.state.repo_root
            )
        except StoreError as exc:
            raise HTTPException(status_code=store_status(exc), detail=str(exc)) from exc
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.get("status") != "ok":
        # 产物暂存或 DB 删除失败时 delete_records 已回滚；对客户端是服务端故障而非冲突
        logger.error("删除会话 %s 未完成: %s", session_id, result)
        raise HTTPException(status_code=500, detail="删除未完成")
    return {"deleted": session_id}


@router.get("/sessions/{session_id}/messages")
def list_messages(
    session_id: str, request: Request, store: StoreDep, restore: int = 0
) -> dict[str, Any]:
    """返回会话与消息；只读，仅 restore=1 时改写待确认脚本并落库。"""
    session = _require_session(store, session_id)
    messages = store.get_messages(session_id)
    recorder = getattr(
        getattr(request.app.state.agent, "workflow_manager", None), "workflow_recorder", None
    )
    return {
        "session": session,
        "messages": [wire_message(m) for m in messages],
        "pending_script": (
            restore_pending(store, session_id, messages, recorder=recorder) if restore else None
        ),
        "soft_warn": soft_warn_for(store, session_id),
    }


class ConfirmRequest(BaseModel):
    """POST /api/confirm 请求体。action 决定分支；脚本动作可用 message_id 锁定卡片。"""

    session_id: str
    action: str
    message_id: int | None = None
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
    with session_guard(body.session_id):
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
                last = confirm_script(
                    agent, store, body.session_id, body.action, message_id=body.message_id
                )
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
