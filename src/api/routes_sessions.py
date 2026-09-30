"""会话 CRUD、消息恢复端点：SessionStore 逐请求短连接。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.api.chat_service import session_lock
from src.api.events import wire_message
from src.control import compact
from src.control.chat_messages import build_assistant_message
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


class ConfirmRequest(BaseModel):
    """POST /api/confirm 请求体。action 决定分支，data_confirm 需带三项来源信息。"""

    session_id: str
    action: str
    source: str | None = None
    asset_id: str | None = None
    query: str | None = None


# 用同步 def：FastAPI 丢到线程池执行，避免阻塞事件循环上的其它 SSE 流
@router.post("/confirm", tags=["confirm"])
def confirm(body: ConfirmRequest, request: Request) -> dict[str, Any]:
    """HITL 确认端点：脚本确认 / 脚本取消 / 数据下载确认。

    script_regen 不设端点：前端用 pending_script.user_request 直接重发
    /api/chat，与 Streamlit 的「重新生成」不改 DB status 行为一致。
    """
    agent = request.app.state.agent
    store = _store()
    _require_session(store, body.session_id)
    lock = session_lock(body.session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话已有生成任务在进行")
    try:
        if body.action == "data_confirm":
            return _confirm_data(agent, store, body)
        if body.action in ("script_confirm", "script_cancel"):
            return _confirm_script(agent, store, body)
        raise HTTPException(status_code=400, detail=f"未知动作: {body.action}")
    finally:
        lock.release()


def _latest_pending_raw(session_id: str) -> dict[str, Any] | None:
    """从库里反查最新一条带 pending_script 的消息（不做过期改写）。"""
    for msg in reversed(SessionStore().get_messages(session_id)):
        if msg.get("pending_script"):
            return msg["pending_script"]
    return None


def _confirm_script(agent: Any, store: SessionStore,
                    body: ConfirmRequest) -> dict[str, Any]:
    pending = _latest_pending_raw(body.session_id)
    if pending is None or pending.get("status") != "pending":
        raise HTTPException(status_code=409, detail="没有待确认的脚本")

    if body.action == "script_cancel":
        store.set_script_status(body.session_id, "cancelled")
        content = "已取消本次脚本执行。"
        results = None
    else:
        result = agent.execute_confirmed_script(
            pending.get("analysis_type"),
            pending.get("params") or {},
            pending.get("script", ""),
            method_context=pending.get("method_context"),
        )
        store.set_script_status(body.session_id, "confirmed")
        content = build_assistant_message(result)["content"]
        results = result.get("results")

    store.append_message(body.session_id, "assistant", content, results=results)
    return {"message": wire_message(store.get_messages(body.session_id)[-1])}


def _confirm_data(agent: Any, store: SessionStore,
                  body: ConfirmRequest) -> dict[str, Any]:
    if not body.source or not body.asset_id:
        raise HTTPException(status_code=400, detail="缺少 source 或 asset_id")

    result = agent.confirm_and_download(body.source, body.asset_id,
                                        query=body.query or "")
    asset = result.get("asset")
    if asset:
        session = store.get_session(body.session_id) or {}
        assets = list(session.get("downloaded_assets") or [])
        assets.append(asset)
        store.update_session_meta(body.session_id, downloaded_assets=assets)
        content = f"已下载 {body.asset_id} → `{asset.get('access_path')}`"
    else:
        content = result.get("message", "数据下载未完成。")

    store.append_message(body.session_id, "assistant", content)
    return {"message": wire_message(store.get_messages(body.session_id)[-1])}
