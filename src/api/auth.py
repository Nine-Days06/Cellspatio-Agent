"""访客工作区身份：高熵 cookie → 哈希 token → 独立临时工作区。"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.deps import get_store
from src.ui.session_store import SessionStore
from src.ui.workspace_cleanup import MAX_WORKSPACE_BYTES, workspace_usage_bytes

VISITOR_COOKIE = "cellspatio_visitor"
ACCOUNT_COOKIE = "cellspatio_account"
EPHEMERAL_TTL = timedelta(hours=24)
ACCOUNT_TTL = timedelta(days=7)
TOKEN_BYTES = 32

_SKIP_PREFIXES = ("/assets/", "/favicon.ico", "/health")
_SKIP_EXACT = {"/api/workspace/new", "/api/auth/login", "/api/auth/logout"}


def _utcnow() -> datetime:
    """返回 UTC 当前时间，统一所有到期判断的时区。"""
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str | None) -> datetime | None:
    """解析 ISO 时间；无时区按 UTC，坏值返回 None。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def hash_token(token: str) -> str:
    """token 只以 SHA-256 形式落库，数据库泄露不能直接复用 cookie。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _workspace_expired(workspace: dict[str, Any], now: datetime) -> bool:
    """非 active 一律视为不可用；临时工作区按 expires_at 判断。"""
    if workspace.get("workspace_status") != "active":
        return True
    if workspace.get("workspace_kind") != "ephemeral":
        return False
    expires_at = _parse_timestamp(workspace.get("workspace_expires_at"))
    return expires_at is not None and expires_at <= now


def _workspace_from_token(row: dict[str, Any]) -> dict[str, Any]:
    """把 token JOIN 查询行还原成中间件使用的 workspace dict。"""
    return {
        **row,
        "id": row["workspace_id"],
        "kind": row["workspace_kind"],
        "label": row["workspace_label"],
        "root_path": row["workspace_root_path"],
        "status": row["workspace_status"],
        "created_at": row["workspace_created_at"],
        "expires_at": row["workspace_expires_at"],
    }


def _cookie_secure(request: Request) -> bool:
    """HTTPS 或 nginx 转发 HTTPS 时给 cookie 加 Secure。"""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return request.url.scheme == "https" or forwarded == "https"


def _set_token_cookie(
    response: Response,
    request: Request,
    name: str,
    token: str,
    max_age: int,
) -> None:
    """统一 HttpOnly/SameSite=Lax 与可选 Secure 的 cookie 写入。"""
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )


def set_visitor_cookie(response: Response, request: Request, token: str) -> None:
    """供 /api/workspace/new 复用同一 cookie 属性。"""
    _set_token_cookie(response, request, VISITOR_COOKIE, token, int(EPHEMERAL_TTL.total_seconds()))


def set_account_cookie(response: Response, request: Request, token: str) -> None:
    """账户登录 cookie；访客 cookie 保留，退出后可回到临时工作区。"""
    _set_token_cookie(response, request, ACCOUNT_COOKIE, token, int(ACCOUNT_TTL.total_seconds()))


def clear_account_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        ACCOUNT_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )


def create_visitor_workspace(
    store: SessionStore, repo_root: str | Path
) -> tuple[dict[str, Any], str]:
    """新建临时工作区，返回工作区行与只写入 cookie 的明文 token。"""
    now = _utcnow()
    expires_at = (now + EPHEMERAL_TTL).isoformat()
    workspace_id = store.create_workspace(
        kind="ephemeral",
        label="临时体验空间",
        expires_at=expires_at,
    )
    root = (Path(repo_root) / "data" / "workspaces" / workspace_id).resolve()
    store.set_workspace_root(workspace_id, str(root))
    token = secrets.token_urlsafe(TOKEN_BYTES)
    store.create_access_token(
        hash_token(token),
        "visitor",
        workspace_id,
        workspace_id,
        expires_at,
    )
    workspace = store.get_workspace(workspace_id)
    assert workspace is not None
    return workspace, token


def _resolve_workspace(
    request: Request, store: SessionStore, repo_root: str | Path
) -> tuple[dict[str, Any], str | None, str | None, bool]:
    """优先账户 token，其次访客 token；返回工作区、待下发 token、身份与到期标记。"""
    now = _utcnow()
    for cookie_name, principal_kind in (
        (ACCOUNT_COOKIE, "account"),
        (VISITOR_COOKIE, "visitor"),
    ):
        raw = request.cookies.get(cookie_name)
        if not raw:
            continue
        row = store.get_access_token(hash_token(raw))
        if row is None or row["principal_kind"] != principal_kind:
            continue
        if principal_kind == "account":
            account = store.get_account(row["principal_id"])
            if account is None or account.get("disabled"):
                continue
        token_expires = _parse_timestamp(row["expires_at"])
        if token_expires is None or token_expires <= now:
            if row["workspace_kind"] == "ephemeral" and row["workspace_status"] == "active":
                return _workspace_from_token(row), None, principal_kind, True
            continue
        if _workspace_expired(row, now):
            return _workspace_from_token(row), None, principal_kind, True
        workspace = _workspace_from_token(row)
        store.touch_workspace(workspace["id"])
        return workspace, None, principal_kind, False
    workspace, token = create_visitor_workspace(store, repo_root)
    return workspace, token, "visitor", False


class WorkspaceMiddleware(BaseHTTPMiddleware):
    """为每个浏览器会话挂载工作区上下文，并把新 cookie 写回响应。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """解析身份、拦截到期/超额写请求，并在新访客时下发 cookie。"""
        if request.url.path.startswith(_SKIP_PREFIXES) or request.url.path in _SKIP_EXACT:
            return await call_next(request)
        store: SessionStore = get_store(request)
        workspace, token, principal_kind, expired = _resolve_workspace(
            request, store, request.app.state.repo_root
        )
        workspace = {
            **workspace,
            "principal_kind": principal_kind,
        }
        request.state.workspace = workspace
        request.state.workspace_id = workspace["id"]
        store.workspace_id = workspace["id"]
        request.state.principal_kind = workspace["principal_kind"]
        request.state.workspace_expired = expired
        if expired and request.url.path.startswith("/api"):
            if request.url.path != "/api/workspace/new" and not request.url.path.startswith(
                "/api/auth/"
            ):
                return JSONResponse(
                    status_code=410,
                    content={
                        "detail": "临时工作区已到期；请重新试用",
                        "code": "workspace_expired",
                    },
                )
        if (
            request.method in {"POST", "PUT", "PATCH"}
            and request.url.path.startswith("/api")
            and request.url.path != "/api/workspace/new"
            and workspace.get("kind") == "ephemeral"
            and workspace_usage_bytes(store, workspace["id"]) >= MAX_WORKSPACE_BYTES
        ):
            return JSONResponse(
                status_code=413,
                content={"detail": "临时工作区总占用已达 2 GB", "code": "workspace_quota"},
            )
        response = await call_next(request)
        if token and principal_kind:
            cookie_name = ACCOUNT_COOKIE if principal_kind == "account" else VISITOR_COOKIE
            max_age = int(
                (ACCOUNT_TTL if principal_kind == "account" else EPHEMERAL_TTL).total_seconds()
            )
            _set_token_cookie(response, request, cookie_name, token, max_age)
        return response


def workspace_context(request: Request) -> dict[str, Any]:
    """读取中间件挂载的工作区；缺失时返回空 dict，供只读端点降级。"""
    return getattr(request.state, "workspace", {}) or {}


def is_ephemeral_workspace(request: Request) -> bool:
    """当前请求是否落在 24 小时临时工作区。"""
    return workspace_context(request).get("kind") == "ephemeral"


def workspace_expiry_view(workspace: dict[str, Any]) -> dict[str, Any]:
    """对外暴露的工作区元数据，不包含 token 或内部 owner。"""
    expires_at = workspace.get("expires_at")
    remaining_seconds: int | None = None
    parsed = _parse_timestamp(expires_at)
    if parsed is not None:
        remaining_seconds = max(0, int((parsed - _utcnow()).total_seconds()))
    return {
        "id": workspace.get("id"),
        "kind": workspace.get("kind"),
        "label": workspace.get("label") or "",
        "created_at": workspace.get("created_at"),
        "expires_at": expires_at,
        "remaining_seconds": remaining_seconds,
        "expired": bool(workspace.get("status") != "active" or remaining_seconds == 0),
        "principal_kind": workspace.get("principal_kind") or "visitor",
    }
