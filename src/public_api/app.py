"""公共证据服务应用工厂：独立于本地客户端的 FastAPI 进程。

事实源仍是 `GovernanceStore` 的 SQLite；本层只负责认证、粗粒度角色门槛与 HTTP 语义。
部署到公网时必须在反向代理终止 TLS，Bearer token 不覆盖明文 HTTP。
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.evidence.accounts import AccountStore, public_user
from src.evidence.errors import ErrorKind, EvidenceError
from src.evidence.governance import (
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    GovernanceStore,
    public_evidence_dir,
)
from src.evidence.vocabulary import vocabulary
from src.public_api.context import reviewer_context
from src.public_api.evidence_service import PublicEvidenceService
from src.public_api.login_limiter import LoginLimiter

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _session_days() -> int:
    """读取 token 有效期天数，非法值回退 7 天。"""
    try:
        return max(1, int(os.environ.get("CELLSPATIO_EVIDENCE_SESSION_DAYS", "7")))
    except ValueError:
        return 7


def _client_ip(request: Request) -> str:
    """只有显式声明信任反向代理时才读 X-Forwarded-For 最后一段。"""
    trusted = os.environ.get("CELLSPATIO_TRUST_PROXY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if trusted:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class LoginRequest(BaseModel):
    """登录请求体。"""

    username: str
    password: str


class SubmitRequest(BaseModel):
    """上传请求体：客户端已预览的公开发送内容 + 显式同意。"""

    preview: dict[str, Any]
    consent: bool = False


class PreviewRequest(BaseModel):
    """上传前预览请求：只收证据卡内容与可选修订引用。"""

    card: dict[str, Any]
    revises_submission_id: str | None = None


class ReviewRequest(BaseModel):
    """复核请求体。"""

    action: str
    checklist: dict[str, bool] | None = None
    reason: str | None = None


class WithdrawRequest(BaseModel):
    """撤销请求体。"""

    reason: str


_KIND_STATUS: dict[ErrorKind, int] = {
    "invalid_input": 400,
    "submission_not_found": 404,
    "permission_denied": 403,
    "duplicate": 409,
    "invalid_state": 409,
    "quota_exceeded": 429,
}


def create_public_app(data_root: str | Path) -> FastAPI:
    """构建公共证据服务；`data_root` 与本地客户端数据根可相同但目录隔离。"""
    public_dir = public_evidence_dir(data_root)
    app = FastAPI(title="CellSpatio Public Evidence API")
    app.state.governance = GovernanceStore(public_dir)
    app.state.accounts = AccountStore(public_dir)
    app.state.evidence_service_factory = PublicEvidenceService
    app.state.login_limiter = LoginLimiter()

    def governance(request: Request) -> GovernanceStore:
        """取当前应用的治理库。"""
        return request.app.state.governance

    def accounts(request: Request) -> AccountStore:
        """取当前应用的账号库。"""
        return request.app.state.accounts

    def current_user(request: Request) -> dict[str, Any]:
        """解析 Bearer token；缺失或过期一律 401。"""
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="请先登录")
        user = accounts(request).get_user_by_token(header[7:].strip())
        if user is None:
            raise HTTPException(status_code=401, detail="登录已失效")
        return user

    def require_roles(*roles: str) -> Callable[[Request], dict[str, Any]]:
        """要求账号至少具备一个指定角色。"""

        def dependency(request: Request) -> dict[str, Any]:
            """解析登录态并校验角色。"""
            user = current_user(request)
            if not set(user["roles"]) & set(roles):
                raise HTTPException(status_code=403, detail="角色不足")
            return user

        return dependency

    CurrentUser = Annotated[dict[str, Any], Depends(current_user)]
    Contributor = Annotated[dict[str, Any], Depends(require_roles(ROLE_CONTRIBUTOR))]
    Reviewer = Annotated[dict[str, Any], Depends(require_roles(ROLE_DOMAIN, ROLE_COMPLIANCE))]
    Store = Annotated[GovernanceStore, Depends(governance)]

    def evidence_service(request: Request, store: Store) -> PublicEvidenceService:
        """按工厂构造服务；测试可替换 `app.state.evidence_service_factory`。"""
        return request.app.state.evidence_service_factory(store)

    EvidenceServiceDep = Annotated[PublicEvidenceService, Depends(evidence_service)]

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        """领域失败按 kind 映射为稳定 4xx；普通 ValueError 仍兜底为 400。"""
        kind = exc.kind if isinstance(exc, EvidenceError) else None
        content: dict[str, str] = {"detail": str(exc)}
        if kind is not None:
            content["code"] = kind
        return JSONResponse(status_code=_KIND_STATUS.get(kind, 400), content=content)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """存活探针。"""
        return {"status": "ok"}

    @app.get("/api/public/vocabulary")
    def review_vocabulary() -> dict[str, dict[str, str]]:
        """复核页标签；静态内容，无需登录。"""
        return vocabulary()

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request) -> dict[str, Any]:
        """用户名密码登录，签发 Bearer token。"""
        key = f"{body.username.strip().lower()}|{_client_ip(request)}"
        limiter: LoginLimiter = request.app.state.login_limiter
        if limiter.blocked(key):
            raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")
        user = accounts(request).authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        limiter.clear(key)
        token = accounts(request).create_token(user["id"], days=_session_days())
        return {"token": token, "user": public_user(user)}

    @app.post("/api/auth/logout")
    def logout(request: Request, user: CurrentUser) -> dict[str, bool]:
        """吊销当前 token。"""
        raw = request.headers.get("authorization", "")[7:].strip()
        accounts(request).delete_token(raw)
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: CurrentUser) -> dict[str, Any]:
        """返回当前账号与角色。"""
        return {"user": public_user(user)}

    @app.post("/api/public/evidence/submissions")
    def submit(
        body: SubmitRequest,
        user: Contributor,
        service: EvidenceServiceDep,
    ) -> dict[str, Any]:
        """上传预览内容；服务端用登录身份覆盖贡献者与时间。"""
        return service.submit(body.preview, body.consent, user)

    @app.get("/api/public/evidence/queue")
    def queue(
        user: Reviewer,
        service: EvidenceServiceDep,
    ) -> dict[str, Any]:
        """复核队列：待审、本角色未签、本人未审，排除自己的提交。"""
        return service.queue(user)

    @app.post("/api/public/evidence/preview")
    def preview(
        body: PreviewRequest,
        user: Contributor,
        service: EvidenceServiceDep,
    ) -> dict[str, Any]:
        """用服务端身份生成上传前预览；修订归属与隐私校验失败直接 4xx。"""
        return service.preview(body.card, user, body.revises_submission_id)

    @app.get("/api/public/evidence/mine")
    def mine(user: Contributor, store: Store) -> dict[str, Any]:
        """贡献者查看本人全部提交状态与最近一次原因。"""
        return {"submissions": store.submissions_by_contributor(user["id"])}

    @app.post("/api/public/evidence/submissions/{evidence_id}/review")
    def review(
        evidence_id: str,
        body: ReviewRequest,
        user: Reviewer,
        service: EvidenceServiceDep,
    ) -> dict[str, Any]:
        """记录领域或合规复核；动作与角色必须匹配。"""
        return service.review(evidence_id, body, user)

    @app.post("/api/public/evidence/submissions/{evidence_id}/withdraw")
    def withdraw(
        evidence_id: str,
        body: WithdrawRequest,
        user: CurrentUser,
        service: EvidenceServiceDep,
    ) -> dict[str, Any]:
        """贡献者撤回自己的提交，或合规复核者强制撤销。"""
        return service.withdraw(evidence_id, body, user)

    @app.get("/api/public/evidence/search")
    def search(
        store: Store,
        q: str = "",
        gate: str | None = None,
    ) -> dict[str, Any]:
        """公开检索：只返回 accepted 证据。"""
        results = store.search(q, gate)
        return {"count": len(results), "results": results}

    @app.get("/api/public/evidence/{evidence_id}/citation")
    def citation(evidence_id: str, service: EvidenceServiceDep) -> dict[str, Any]:
        """引用索引：只对 accepted 证据给出可引用条目；撤销后 404。"""
        return service.citation(evidence_id)

    @app.get("/api/public/evidence/{evidence_id}/events")
    def events(
        evidence_id: str,
        user: Reviewer,
        service: EvidenceServiceDep,
    ) -> dict[str, Any]:
        """复核者查看审计事件，并一次返回当前查看者的复核能力。"""
        return service.events(evidence_id, user)

    @app.get("/api/public/evidence/{evidence_id}")
    def get_evidence(
        evidence_id: str,
        store: Store,
        request: Request,
        unpublished: bool = False,
    ) -> dict[str, Any]:
        """公开读取 accepted 全文；其它状态只返回墓碑。"""
        if not unpublished:
            return store.get(evidence_id)
        user = current_user(request)
        ctx = reviewer_context(user)
        return store.get(
            evidence_id,
            include_unaccepted=True,
            viewer_actor=ctx.actor_id,
            viewer_role=ctx.role,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """未结构化异常统一 500 JSON。"""
        logger.exception("unhandled public evidence error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "服务内部错误"})

    if _STATIC_DIR.is_dir():
        app.mount("/review", StaticFiles(directory=_STATIC_DIR, html=True), name="review")

    return app
