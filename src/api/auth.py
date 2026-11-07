"""账号密码认证：argon2 哈希 + 服务端 token cookie，会话按 owner 隔离。

默认开启；`CELLSPATIO_AUTH_DISABLED=1` 仅供测试，线上禁用即等于裸奔。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.ui.session_store import SessionStore

logger = logging.getLogger(__name__)

COOKIE_NAME = "cellspatio_session"
_SESSION_RE = re.compile(r"^/api/sessions/([^/]+)")
_PUBLIC_API = {"/api/auth/login"}

_hasher = PasswordHasher()
_attempts: dict[str, list[float]] = {}
_attempts_guard = threading.Lock()
_LIMIT_WINDOW = 300.0
_LIMIT_MAX = 5


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cookie_secure() -> bool:
    return os.environ.get("CELLSPATIO_COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes")


def _session_days() -> int:
    try:
        return max(1, int(os.environ.get("CELLSPATIO_SESSION_DAYS", "7")))
    except ValueError:
        return 7


def _auth_disabled() -> bool:
    return os.environ.get("CELLSPATIO_AUTH_DISABLED", "0").strip().lower() in ("1", "true", "yes")


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "is_admin": bool(user.get("is_admin")),
    }


def _rate_limited(key: str) -> bool:
    """shortcut: 内存限速，进程重启清零；长期防护交给反代 fail2ban。"""
    now = time.monotonic()
    with _attempts_guard:
        hits = [t for t in _attempts.get(key, []) if now - t < _LIMIT_WINDOW]
        if len(hits) >= _LIMIT_MAX:
            _attempts[key] = hits
            return True
        hits.append(now)
        _attempts[key] = hits
        return False


def _clear_attempts(key: str) -> None:
    with _attempts_guard:
        _attempts.pop(key, None)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_origin(request: Request) -> None:
    """反代后只认配置站点；未配置时跳过（本机开发）。"""
    site = os.environ.get("CELLSPATIO_SITE_URL", "").strip().rstrip("/")
    if not site or request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin", "").rstrip("/")
    if origin and origin != site:
        raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def current_user(request: Request) -> dict[str, Any]:
    """中间件已保证已登录；取 state 里的用户字典。"""
    return request.state.user


def require_admin(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    store = SessionStore()
    key = f"{body.username.strip().lower()}|{_client_ip(request)}"
    if _rate_limited(key):
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")
    user = store.get_user_by_username(body.username)
    if (
        user is None
        or user.get("disabled")
        or not verify_password(user["password_hash"], body.password)
    ):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _clear_attempts(key)
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=_session_days())
    store.create_token(token_hash(token), user["id"], expires.isoformat())
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_session_days() * 86400,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    return {"user": _public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        SessionStore().delete_token(token_hash(token))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    return {"user": _public_user(current_user(request))}


@router.post("/password")
def change_password(body: PasswordRequest, request: Request) -> dict[str, Any]:
    store = SessionStore()
    user = current_user(request)
    if not verify_password(user["password_hash"], body.old_password):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    store.update_user_password(user["id"], hash_password(body.new_password))
    token = request.cookies.get(COOKIE_NAME)
    store.delete_user_tokens(user["id"], keep=token_hash(token) if token else None)
    return {"ok": True}


def install_auth(app: FastAPI) -> None:
    """全局中间件：解析 cookie、拦截未登录 API、按路径校验会话归属。"""

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if _auth_disabled():
            request.state.user = {
                # id=None：owner 落 NULL，避免 FK 要求 users 表里有匿名用户
                "id": None,
                "username": "anonymous",
                "display_name": "anonymous",
                "is_admin": True,
            }
            return await call_next(request)
        if path in _PUBLIC_API:
            try:
                _check_origin(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            return await call_next(request)

        try:
            _check_origin(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        token = request.cookies.get(COOKIE_NAME)
        user = SessionStore().get_user_by_token(token_hash(token)) if token else None
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        request.state.user = user

        match = _SESSION_RE.match(path)
        if match and SessionStore().get_session_for(match.group(1), user["id"]) is None:
            return JSONResponse(status_code=404, content={"detail": "会话不存在"})
        return await call_next(request)


def ensure_admin() -> None:
    """首次启动用 env 建管理员：CELLSPATIO_ADMIN_USER / _PASSWORD。"""
    username = os.environ.get("CELLSPATIO_ADMIN_USER", "").strip()
    password = os.environ.get("CELLSPATIO_ADMIN_PASSWORD", "")
    if not username or not password:
        return
    store = SessionStore()
    if store.get_user_by_username(username) or store.list_users():
        return
    store.create_user(username, hash_password(password), username, is_admin=True)
    logger.info("bootstrap admin user created: %s", username)
