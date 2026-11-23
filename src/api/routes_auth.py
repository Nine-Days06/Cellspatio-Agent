"""管理账户登录/退出：不提供注册、找回密码或用户管理。"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.api.auth import (
    ACCOUNT_COOKIE,
    ACCOUNT_TTL,
    clear_account_cookie,
    hash_token,
    set_account_cookie,
    workspace_expiry_view,
)
from src.api.deps import StoreDep
from src.public_api.login_limiter import LoginLimiter
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/auth", tags=["auth"])

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("not-a-real-account")


class LoginRequest(BaseModel):
    """登录表单；用户名与密码只用于本次校验。"""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


def _client_key(request: Request, username: str) -> str:
    """限速键取真实客户端 IP 与规范化用户名，避免按账户整体封禁。"""
    host = request.client.host if request.client else "unknown"
    # 只有本机反代（nginx）来的 X-Real-IP 可信，外部直连不得自报来源。
    if host in {"127.0.0.1", "::1"}:
        host = request.headers.get("x-real-ip") or host
    return f"{host}:{username.strip().lower()}"


def _workspace_view(store: SessionStore, account: dict[str, Any]) -> dict[str, Any]:
    """把账户的永久工作区投影成前端可直接展示的元数据。"""
    workspace = store.get_workspace(account["workspace_id"])
    if workspace is None:
        raise HTTPException(409, "账户工作区不存在")
    return workspace_expiry_view(
        {
            "id": workspace["id"],
            "kind": workspace["kind"],
            "label": account.get("display_name") or account["username"],
            "created_at": workspace["created_at"],
            "expires_at": workspace["expires_at"],
            "status": workspace["status"],
            "principal_kind": "account",
        }
    )


@router.post("/login")
def login(
    body: LoginRequest, request: Request, response: Response, store: StoreDep
) -> dict[str, Any]:
    """登录成功轮换账户 token；失败响应不区分账户不存在与密码错误。"""
    key = _client_key(request, body.username)
    limiter: LoginLimiter = request.app.state.login_limiter
    if limiter.blocked(key):
        raise HTTPException(429, "登录尝试过多，请稍后再试")

    account = store.get_account_by_username(body.username.strip())
    candidate = account["password_hash"] if account else _DUMMY_HASH
    try:
        _hasher.verify(candidate, body.password)
        verified = account is not None
    except (VerifyMismatchError, InvalidHashError):
        verified = False
    if not verified or account is None or account.get("disabled"):
        raise HTTPException(401, "用户名或密码错误")
    if account.get("workspace_status") != "active":
        raise HTTPException(409, "账户工作区不可用")

    limiter.clear(key)
    token = secrets.token_urlsafe(32)
    expiry = (datetime.now(timezone.utc) + ACCOUNT_TTL).isoformat()
    store.create_access_token(
        hash_token(token),
        "account",
        account["id"],
        account["workspace_id"],
        expiry,
    )
    set_account_cookie(response, request, token)
    return {
        "workspace": _workspace_view(store, account),
        "account": {"username": account["username"]},
    }


@router.post("/logout")
def logout(request: Request, response: Response, store: StoreDep) -> dict[str, Any]:
    """吊销当前账户 token；访客 cookie 不动，退出后回到临时工作区。"""
    raw = request.cookies.get(ACCOUNT_COOKIE)
    if raw:
        store.delete_access_token(hash_token(raw))
    clear_account_cookie(response, request)
    return {"ok": True}
