"""公共证据服务应用工厂：独立于本地客户端的 FastAPI 进程。

事实源仍是 `GovernanceStore` 的 SQLite；本层只负责认证、角色校验与 HTTP 语义。
部署到公网时必须在反向代理终止 TLS，Bearer token 不覆盖明文 HTTP。
"""

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.evidence.accounts import AccountStore, public_user
from src.evidence.contribution import build_envelope, preview_card
from src.evidence.governance import (
    ACTION_ROLE,
    COMPLIANCE_CHECKLIST,
    DOMAIN_CHECKLIST,
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    STATE_NEEDS_CHANGES,
    GovernanceStore,
    public_evidence_dir,
)
from src.evidence.schema import EvidenceCard

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_LIMIT_WINDOW = 300.0
_LIMIT_MAX = 5
_attempts: dict[str, list[float]] = {}
_attempts_guard = threading.Lock()


def _session_days() -> int:
    """读取 token 有效期天数，非法值回退 7 天。"""
    try:
        return max(1, int(os.environ.get("CELLSPATIO_EVIDENCE_SESSION_DAYS", "7")))
    except ValueError:
        return 7


def _client_ip(request: Request) -> str:
    """只有显式声明信任反向代理时才读转发头，否则用直连地址。"""
    trusted = os.environ.get("CELLSPATIO_TRUST_PROXY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if trusted:
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(key: str) -> bool:
    """内存登录限速；进程重启清零，长期防护交给反向代理。"""
    now = time.monotonic()
    with _attempts_guard:
        # shortcut: 内存限速只防短期爆破；键超过 1024 个时清理过期项，避免随机用户名撑大内存。
        if len(_attempts) > 1024:
            for stale in [
                k for k, v in _attempts.items() if not any(now - t < _LIMIT_WINDOW for t in v)
            ]:
                _attempts.pop(stale, None)
        hits = [t for t in _attempts.get(key, []) if now - t < _LIMIT_WINDOW]
        if len(hits) >= _LIMIT_MAX:
            _attempts[key] = hits
            return True
        hits.append(now)
        _attempts[key] = hits
        return False


def _clear_attempts(key: str) -> None:
    """登录成功后清空该 key 的失败记录。"""
    with _attempts_guard:
        _attempts.pop(key, None)


def _http_error(exc: ValueError) -> HTTPException:
    """把治理层的 ValueError 映射成稳定的 HTTP 状态码。"""
    # shortcut: 依赖治理层错误文案；改文案时必须同步这里，或改为结构化错误码。
    message = str(exc)
    if any(
        marker in message
        for marker in (
            "不得审核自己的提交",
            "不得读取自己的未发布全文",
            "只有复核者可读取未发布全文",
            "只有复核者可查看审核队列",
            "只能撤回自己的提交",
            "只有贡献者或合规复核者可撤销",
        )
    ):
        return HTTPException(status_code=403, detail=message)
    if "不存在" in message:
        return HTTPException(status_code=404, detail=message)
    if any(
        marker in message
        for marker in (
            "已存在",
            "只能提交一次",
            "该角色已经通过",
            "只能修订",
            "只有 pending 提交可审核",
            "只有 accepted 提交可撤销",
        )
    ):
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=400, detail=message)


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


def _reviewer_role(user: dict[str, Any]) -> str:
    """取账号的复核角色；优先领域，其次合规。"""
    if ROLE_DOMAIN in user["roles"]:
        return ROLE_DOMAIN
    if ROLE_COMPLIANCE in user["roles"]:
        return ROLE_COMPLIANCE
    raise HTTPException(status_code=403, detail="只有复核者可读取未发布全文")


def create_public_app(data_root: str | Path) -> FastAPI:
    """构建公共证据服务；`data_root` 与本地客户端数据根可相同但目录隔离。"""
    public_dir = public_evidence_dir(data_root)
    app = FastAPI(title="CellSpatio Public Evidence API")
    app.state.governance = GovernanceStore(public_dir)
    app.state.accounts = AccountStore(public_dir)

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

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        """路由内未转换的 ValueError 也走稳定的 4xx，而不是 500。"""
        error = _http_error(exc)
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """存活探针。"""
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request) -> dict[str, Any]:
        """用户名密码登录，签发 Bearer token。"""
        key = f"{body.username.strip().lower()}|{_client_ip(request)}"
        if _rate_limited(key):
            raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试")
        user = accounts(request).authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        _clear_attempts(key)
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
        store: Store,
    ) -> dict[str, Any]:
        """上传预览内容；服务端用登录身份覆盖贡献者与时间。"""
        envelope = build_envelope(body.preview, consent=body.consent)
        envelope.contributor_id = user["id"]
        envelope.submitted_at = None
        return store.submit(envelope)

    @app.get("/api/public/evidence/queue")
    def queue(
        user: Reviewer,
        store: Store,
    ) -> dict[str, Any]:
        """复核队列：待审、本角色未签、本人未审，排除自己的提交。"""
        role = _reviewer_role(user)
        return {
            "role": role,
            "queue": store.review_queue(viewer_actor=user["id"], viewer_role=role),
        }

    @app.get("/api/public/evidence/checklists")
    def checklists(user: Reviewer) -> dict[str, list[str]]:
        """复核清单键；前端标签自行映射，避免复制治理层常量。"""
        return {
            ROLE_DOMAIN: list(DOMAIN_CHECKLIST),
            ROLE_COMPLIANCE: list(COMPLIANCE_CHECKLIST),
        }

    @app.post("/api/public/evidence/preview")
    def preview(body: PreviewRequest, user: Contributor, store: Store) -> dict[str, Any]:
        """用服务端身份生成上传前预览；修订归属与隐私校验失败直接 4xx。"""
        if body.revises_submission_id:
            original = store.summary(body.revises_submission_id)
            if original["contributor_id"] != user["id"]:
                raise ValueError("只能修订自己的提交")
            if original["status"] != STATE_NEEDS_CHANGES:
                raise ValueError("只能修订 needs_changes 的提交")
        card = EvidenceCard.model_validate(body.card)
        return preview_card(
            card,
            contributor_id=user["id"],
            revises_submission_id=body.revises_submission_id,
        )

    @app.get("/api/public/evidence/mine")
    def mine(user: Contributor, store: Store) -> dict[str, Any]:
        """贡献者查看本人全部提交状态与最近一次原因。"""
        return {"submissions": store.submissions_by_contributor(user["id"])}

    @app.post("/api/public/evidence/submissions/{evidence_id}/review")
    def review(
        evidence_id: str,
        body: ReviewRequest,
        user: Reviewer,
        store: Store,
    ) -> dict[str, Any]:
        """记录领域或合规复核；动作与角色必须匹配。"""
        role = ACTION_ROLE.get(body.action)
        if role is None:
            raise HTTPException(status_code=400, detail="未知审核动作")
        if role not in user["roles"]:
            raise HTTPException(status_code=403, detail=f"动作 {body.action} 需要角色 {role}")
        return store.review(
            evidence_id,
            actor_id=user["id"],
            role=role,
            action=body.action,
            checklist=body.checklist,
            reason=body.reason,
        )

    @app.post("/api/public/evidence/submissions/{evidence_id}/withdraw")
    def withdraw(
        evidence_id: str,
        body: WithdrawRequest,
        user: CurrentUser,
        store: Store,
    ) -> dict[str, Any]:
        """贡献者撤回自己的提交，或合规复核者强制撤销。"""
        summary = store.summary(evidence_id)
        if user["id"] == summary["contributor_id"]:
            role = ROLE_CONTRIBUTOR
        elif ROLE_COMPLIANCE in user["roles"]:
            role = ROLE_COMPLIANCE
        else:
            raise HTTPException(status_code=403, detail="只能撤回自己的提交")
        return store.withdraw(evidence_id, actor_id=user["id"], role=role, reason=body.reason)

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
    def citation(evidence_id: str, store: Store) -> dict[str, Any]:
        """引用索引：只对 accepted 证据给出可引用条目；撤销后 404。"""
        data = store.get(evidence_id)
        card = data.get("card")
        if card is None:
            raise HTTPException(status_code=404, detail="证据已撤销或不可引用")
        parsed = EvidenceCard.model_validate(card)
        return {
            "evidence_id": data["evidence_id"],
            "card_id": data["card_id"],
            "gate": data["gate"],
            "claim": parsed.claim.text,
            "source_ids": list(parsed.provenance.source_ids),
            "license": parsed.license.model_dump(),
            "accepted_at": data["accepted_at"],
            "superseded_by": data["superseded_by"],
            "citation": (
                f"{parsed.claim.text} "
                f"[{parsed.license.attribution}; CellSpatio evidence {data['evidence_id']}]"
            ),
        }

    @app.get("/api/public/evidence/{evidence_id}/events")
    def events(
        evidence_id: str,
        user: Reviewer,
        store: Store,
    ) -> dict[str, Any]:
        """复核者查看完整审计事件。"""
        return {"events": store.events(evidence_id)}

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
        role = _reviewer_role(user)
        return store.get(
            evidence_id,
            include_unaccepted=True,
            viewer_actor=user["id"],
            viewer_role=role,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """未结构化异常统一 500 JSON。"""
        logger.exception("unhandled public evidence error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "服务内部错误"})

    if _STATIC_DIR.is_dir():
        app.mount("/review", StaticFiles(directory=_STATIC_DIR, html=True), name="review")

    return app
