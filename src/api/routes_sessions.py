"""会话 CRUD、消息恢复端点：SessionStore 逐请求短连接。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.events import wire_message
from src.control import compact
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["sessions"])


def _store() -> SessionStore:
    """每次新建短连接存储（与 src/ui/app.py:_get_store 同策略）。"""
    return SessionStore()


def _require_session(store: SessionStore, session_id: str) -> dict[str, Any]:
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


class CreateSessionRequest(BaseModel):
    """POST /api/sessions 请求体：title 缺省时由 store 生成时间戳标题。"""

    title: str | None = None


@router.get("/sessions")
def list_sessions() -> dict[str, Any]:
    return {"sessions": _store().list_sessions()}


@router.post("/sessions")
def create_session(body: CreateSessionRequest | None = None) -> dict[str, Any]:
    store = _store()
    title = (body.title if body and body.title else "新会话")
    session_id = store.create_session(title)
    return {"session": store.get_session(session_id)}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, Any]:
    store = _store()
    _require_session(store, session_id)
    store.delete_session(session_id)
    return {"deleted": session_id}


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, restore: int = 0) -> dict[str, Any]:
    store = _store()
    session = _require_session(store, session_id)
    messages = store.get_messages(session_id)
    soft_warn = compact.should_soft_warn(
        session.get("summary"), compact.prepare_history(messages)
    )
    return {
        "session": session,
        "messages": [wire_message(m) for m in messages],
        "pending_script": _restore_pending(store, session_id, messages) if restore else None,
        "soft_warn": soft_warn,
    }


def _restore_pending(store: SessionStore, session_id: str,
                     messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """复刻 src/ui/app.py:439 _load_session 的过期语义。

    只看最新一条带 pending_script 的消息：pending → 落库置 expired 后按
    expired 返回；expired → 原样返回；其他状态 → None。
    """
    for msg in reversed(messages):
        pending = msg.get("pending_script")
        if not pending:
            continue
        if pending.get("status") in ("pending", "expired"):
            if pending.get("status") == "pending":
                store.set_script_status(session_id, "expired")
            return {
                "script": pending.get("script", ""),
                "analysis_type": pending.get("analysis_type"),
                "params": pending.get("params") or {},
                "method_context": pending.get("method_context"),
                "user_request": pending.get("user_request", ""),
                "status": "expired",
            }
        # 只看最新一条 pending_script，更早的不再考虑
        return None
    return None
