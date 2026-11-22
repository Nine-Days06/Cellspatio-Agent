"""公共服务雏形：认证、角色、上传、审核队列与引用索引。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from src.evidence.accounts import hash_password
from src.evidence.contribution import (
    PREVIEW_SCHEMA_VERSION,
    _payload_sha256,
    build_envelope,
    preview_card,
)
from src.evidence.errors import EvidenceError
from src.evidence.governance import (
    ACTION_COMPLIANCE_NEEDS_CHANGES,
    ACTION_COMPLIANCE_PASS,
    ACTION_COMPLIANCE_REJECT,
    ACTION_DOMAIN_NEEDS_CHANGES,
    ACTION_DOMAIN_PASS,
    ACTION_DOMAIN_REJECT,
    COMPLIANCE_CHECKLIST,
    DOMAIN_CHECKLIST,
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    GovernanceStore,
)
from src.evidence.public_client import PublicEvidenceClient, PublicEvidenceError
from src.evidence.schema import (
    Claim,
    EvidenceCard,
    License,
    Method,
    Provenance,
    Review,
    Subject,
)
from src.public_api.app import ReviewRequest, WithdrawRequest, create_public_app
from src.public_api.context import ReviewerContext, reviewer_context
from src.public_api.evidence_service import PublicEvidenceService

PASSWORD = "password123"


def _card(card_id: str = "evc-service-1") -> EvidenceCard:
    """构造一张通过隐私与准入检查的最小证据卡。"""
    return EvidenceCard(
        card_id=card_id,
        created_at="2026-11-12T00:00:00+00:00",
        gate="qc_review",
        outcome="accepted",
        subject=Subject(session_id="s", run_id="r", binding_sha256="a" * 64),
        method=Method(tool="sc_qc", tool_version="1", parameters={"min_features": 200}),
        observation={"cells": 10},
        claim=Claim(text="QC 阈值经人工批准"),
        limitations=["探索性结果"],
        provenance=Provenance(source_ids=["GSE12345"]),
        review=Review(status="accepted"),
        license=License(spdx="CC-BY-4.0", attribution="tester"),
    )


def _account(app, username: str, roles: tuple[str, ...], *, disabled: bool = False) -> str:
    """直接写入测试账号并返回 ID。"""
    user_id = app.state.accounts.create_user(username, hash_password(PASSWORD), roles=roles)
    if disabled:
        app.state.accounts.set_disabled(user_id, True)
    return user_id


def _login(client: TestClient, username: str) -> str:
    """登录并返回 token。"""
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _headers(token: str) -> dict[str, str]:
    """Bearer 请求头。"""
    return {"Authorization": f"Bearer {token}"}


def _checks(names: tuple[str, ...]) -> dict[str, bool]:
    """清单全部置 true。"""
    return {name: True for name in names}


@pytest.fixture
def app(tmp_path):
    """每个用例独立的公共库。"""
    return create_public_app(tmp_path)


@pytest.fixture
def client(app) -> TestClient:
    """ASGI 测试客户端。"""
    return TestClient(app)


def test_full_public_loop_over_http(app, client: TestClient):
    """上传 → 审核队列 → 双签 → 检索 → 引用 → 撤销后同步失效。"""
    contributor = _account(app, "contributor-1", (ROLE_CONTRIBUTOR,))
    _account(app, "domain-1", (ROLE_DOMAIN,))
    _account(app, "compliance-1", (ROLE_COMPLIANCE,))
    contributor_token = _login(client, "contributor-1")
    domain_token = _login(client, "domain-1")
    compliance_token = _login(client, "compliance-1")

    preview = preview_card(_card(), contributor_id="local-user")
    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={"preview": preview, "consent": True},
        headers=_headers(contributor_token),
    )
    assert uploaded.status_code == 200, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    assert uploaded.json()["status"] == "pending"
    assert uploaded.json()["contributor_id"] == contributor

    assert client.get("/api/public/evidence/search?q=QC").json()["count"] == 0
    queue = client.get("/api/public/evidence/queue", headers=_headers(domain_token))
    assert [item["evidence_id"] for item in queue.json()["queue"]] == [evidence_id]

    domain = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={
            "action": ACTION_DOMAIN_PASS,
            "checklist": _checks(DOMAIN_CHECKLIST),
        },
        headers=_headers(domain_token),
    )
    assert domain.status_code == 200, domain.text
    assert domain.json()["status"] == "pending"
    accepted = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={
            "action": ACTION_COMPLIANCE_PASS,
            "checklist": _checks(COMPLIANCE_CHECKLIST),
        },
        headers=_headers(compliance_token),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    found = client.get("/api/public/evidence/search?q=QC").json()
    assert [item["evidence_id"] for item in found["results"]] == [evidence_id]
    citation = client.get(f"/api/public/evidence/{evidence_id}/citation")
    assert citation.status_code == 200, citation.text
    assert citation.json()["evidence_id"] == evidence_id
    assert "CellSpatio evidence" in citation.json()["citation"]

    withdrawn = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/withdraw",
        json={"reason": "作者撤回"},
        headers=_headers(contributor_token),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"

    assert client.get("/api/public/evidence/search?q=QC").json()["count"] == 0
    assert client.get(f"/api/public/evidence/{evidence_id}/citation").status_code == 404
    tombstone = client.get(f"/api/public/evidence/{evidence_id}").json()
    assert tombstone["status"] == "withdrawn"
    assert "card" not in tombstone


def test_role_guards_self_review_and_duplicate_card(app, client: TestClient):
    """越权、自批与重复卡都必须在服务端被拒绝。"""
    _account(app, "contributor-2", (ROLE_CONTRIBUTOR,))
    _account(app, "dual-2", (ROLE_CONTRIBUTOR, ROLE_DOMAIN))
    contributor_token = _login(client, "contributor-2")
    dual_token = _login(client, "dual-2")

    preview = preview_card(_card("evc-service-2"), contributor_id="spoofed")
    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={"preview": preview, "consent": True},
        headers=_headers(dual_token),
    )
    assert uploaded.status_code == 200, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    assert uploaded.json()["contributor_id"] != "spoofed"

    own_review = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={
            "action": ACTION_DOMAIN_PASS,
            "checklist": _checks(DOMAIN_CHECKLIST),
        },
        headers=_headers(dual_token),
    )
    assert own_review.status_code == 403
    assert own_review.json()["code"] == "permission_denied"

    no_role = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={
            "action": ACTION_DOMAIN_PASS,
            "checklist": _checks(DOMAIN_CHECKLIST),
        },
        headers=_headers(contributor_token),
    )
    assert no_role.status_code == 403
    assert (
        client.get("/api/public/evidence/queue", headers=_headers(contributor_token)).status_code
        == 403
    )

    duplicate = client.post(
        "/api/public/evidence/submissions",
        json={"preview": preview, "consent": True},
        headers=_headers(dual_token),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate"


def test_anonymous_tombstone_hides_reviewer_identity_and_reason(app, client: TestClient):
    """未发布的墓碑不向匿名者暴露复核账号与拒审理由。"""
    _account(app, "contributor-5", (ROLE_CONTRIBUTOR,))
    _account(app, "domain-5", (ROLE_DOMAIN,))
    contributor_token = _login(client, "contributor-5")
    domain_token = _login(client, "domain-5")

    preview = preview_card(_card("evc-service-5"), contributor_id="local")
    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={"preview": preview, "consent": True},
        headers=_headers(contributor_token),
    )
    evidence_id = uploaded.json()["evidence_id"]

    rejected = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={"action": ACTION_DOMAIN_REJECT, "reason": "内部判断：第三方数据未授权"},
        headers=_headers(domain_token),
    )
    assert rejected.status_code == 200, rejected.text

    tombstone = client.get(f"/api/public/evidence/{evidence_id}").json()
    assert tombstone["status"] == "rejected"
    assert "actor_id" not in tombstone
    assert "reason" not in tombstone

    # 复核者仍能从审计事件看到操作者与理由
    events = client.get(
        f"/api/public/evidence/{evidence_id}/events", headers=_headers(domain_token)
    ).json()["events"]
    assert events[-1]["reason"] == "内部判断：第三方数据未授权"
    assert events[-1]["actor_id"]


def test_reviewer_queue_unpublished_access_and_events(app, client: TestClient):
    """队列排除本人，未发布全文只给复核者，审过即出队。"""
    _account(app, "contributor-3", (ROLE_CONTRIBUTOR,))
    _account(app, "domain-3", (ROLE_DOMAIN,))
    _account(app, "domain-3b", (ROLE_DOMAIN,))
    contributor_token = _login(client, "contributor-3")
    domain_token = _login(client, "domain-3")
    other_domain_token = _login(client, "domain-3b")

    preview = preview_card(_card("evc-service-3"), contributor_id="local")
    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={"preview": preview, "consent": True},
        headers=_headers(contributor_token),
    )
    evidence_id = uploaded.json()["evidence_id"]

    unpublished = client.get(
        f"/api/public/evidence/{evidence_id}?unpublished=1", headers=_headers(domain_token)
    )
    assert unpublished.status_code == 200
    assert unpublished.json()["card"]["card_id"] == "evc-service-3"
    assert (
        client.get(
            f"/api/public/evidence/{evidence_id}?unpublished=1",
            headers=_headers(contributor_token),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/public/evidence/{evidence_id}/events", headers=_headers(contributor_token)
        ).status_code
        == 403
    )

    queue = client.get("/api/public/evidence/queue", headers=_headers(domain_token)).json()
    assert [item["evidence_id"] for item in queue["queue"]] == [evidence_id]
    reviewed = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={
            "action": ACTION_DOMAIN_PASS,
            "checklist": _checks(DOMAIN_CHECKLIST),
        },
        headers=_headers(domain_token),
    )
    assert reviewed.status_code == 200
    assert (
        client.get("/api/public/evidence/queue", headers=_headers(domain_token)).json()["queue"]
        == []
    )
    assert (
        client.get("/api/public/evidence/queue", headers=_headers(other_domain_token)).json()[
            "queue"
        ]
        == []
    )
    events = client.get(
        f"/api/public/evidence/{evidence_id}/events", headers=_headers(domain_token)
    ).json()["events"]
    assert [event["action"] for event in events] == ["submit", ACTION_DOMAIN_PASS]

    again = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={
            "action": ACTION_DOMAIN_PASS,
            "checklist": _checks(DOMAIN_CHECKLIST),
        },
        headers=_headers(domain_token),
    )
    assert again.status_code == 409


def test_events_returns_review_capability_for_current_viewer(app, client: TestClient):
    """事件响应一次给足复核能力；签署后本角色不再出现表单动作。"""
    _account(app, "events-contributor", (ROLE_CONTRIBUTOR,))
    _account(app, "events-domain", (ROLE_DOMAIN,))
    contributor_token = _login(client, "events-contributor")
    domain_token = _login(client, "events-domain")

    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={
            "preview": preview_card(_card("evc-events-1"), contributor_id="local"),
            "consent": True,
        },
        headers=_headers(contributor_token),
    )
    evidence_id = uploaded.json()["evidence_id"]
    url = f"/api/public/evidence/{evidence_id}/events"

    payload = client.get(url, headers=_headers(domain_token)).json()
    assert payload["review"] == {
        "role": ROLE_DOMAIN,
        "actions": [
            {
                "id": ACTION_DOMAIN_PASS,
                "kind": "pass",
                "requires_checklist": True,
                "requires_reason": False,
            },
            {
                "id": ACTION_DOMAIN_NEEDS_CHANGES,
                "kind": "needs_changes",
                "requires_checklist": False,
                "requires_reason": True,
            },
            {
                "id": ACTION_DOMAIN_REJECT,
                "kind": "reject",
                "requires_checklist": False,
                "requires_reason": True,
            },
        ],
        "blocked_reason": None,
        "blocked_code": None,
        "checklists": {ROLE_DOMAIN: list(DOMAIN_CHECKLIST)},
    }
    assert [event["action"] for event in payload["events"]] == ["submit"]
    assert payload["evidence"]["evidence_id"] == evidence_id

    signed = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={"action": ACTION_DOMAIN_PASS, "checklist": _checks(DOMAIN_CHECKLIST)},
        headers=_headers(domain_token),
    )
    assert signed.status_code == 200
    blocked = client.get(url, headers=_headers(domain_token)).json()["review"]
    assert blocked["actions"] == []
    assert blocked["blocked_reason"] == "本角色已签署，等待另一角色复核。"
    assert blocked["blocked_code"] == "role_signed"
    assert blocked["checklists"] == {ROLE_DOMAIN: list(DOMAIN_CHECKLIST)}


def test_events_capability_blocks_self_review_and_finished_state(app, client: TestClient):
    """本人提交与非 pending 状态都在协议结果里阻断，页面不再读事件推导。"""
    _account(app, "events-dual", (ROLE_CONTRIBUTOR, ROLE_DOMAIN))
    _account(app, "events-other-contributor", (ROLE_CONTRIBUTOR,))
    _account(app, "events-domain", (ROLE_DOMAIN,))
    dual_token = _login(client, "events-dual")
    domain_token = _login(client, "events-domain")

    own = client.post(
        "/api/public/evidence/submissions",
        json={
            "preview": preview_card(_card("evc-events-2"), contributor_id="local"),
            "consent": True,
        },
        headers=_headers(dual_token),
    ).json()["evidence_id"]
    own_review = client.get(
        f"/api/public/evidence/{own}/events", headers=_headers(dual_token)
    ).json()["review"]
    assert own_review["actions"] == []
    assert own_review["blocked_reason"] == "贡献者不得审核自己的提交"
    assert own_review["blocked_code"] == "self_review"

    other = client.post(
        "/api/public/evidence/submissions",
        json={
            "preview": preview_card(_card("evc-events-3"), contributor_id="local"),
            "consent": True,
        },
        headers=_headers(_login(client, "events-other-contributor")),
    ).json()["evidence_id"]
    changed = client.post(
        f"/api/public/evidence/submissions/{other}/review",
        json={"action": ACTION_DOMAIN_NEEDS_CHANGES, "reason": "补充限制"},
        headers=_headers(domain_token),
    )
    assert changed.status_code == 200
    finished = client.get(
        f"/api/public/evidence/{other}/events", headers=_headers(domain_token)
    ).json()["review"]
    assert finished["actions"] == []
    assert finished["blocked_reason"] == "该提交已结束审核。"
    assert finished["blocked_code"] == "finished"


def test_withdraw_role_blocks_domain_and_allows_compliance_override(app, client: TestClient):
    """撤回角色只看协议：非本人领域角色 403，合规角色可强撤。"""
    _account(app, "withdraw-contributor", (ROLE_CONTRIBUTOR,))
    _account(app, "withdraw-domain", (ROLE_DOMAIN,))
    _account(app, "withdraw-compliance", (ROLE_COMPLIANCE,))
    contributor_token = _login(client, "withdraw-contributor")
    domain_token = _login(client, "withdraw-domain")
    compliance_token = _login(client, "withdraw-compliance")

    evidence_id = client.post(
        "/api/public/evidence/submissions",
        json={
            "preview": preview_card(_card("evc-withdraw-1"), contributor_id="local"),
            "consent": True,
        },
        headers=_headers(contributor_token),
    ).json()["evidence_id"]
    for token, action, checklist in (
        (domain_token, ACTION_DOMAIN_PASS, DOMAIN_CHECKLIST),
        (compliance_token, ACTION_COMPLIANCE_PASS, COMPLIANCE_CHECKLIST),
    ):
        assert (
            client.post(
                f"/api/public/evidence/submissions/{evidence_id}/review",
                json={"action": action, "checklist": _checks(checklist)},
                headers=_headers(token),
            ).status_code
            == 200
        )

    denied = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/withdraw",
        json={"reason": "越权"},
        headers=_headers(domain_token),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"

    forced = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/withdraw",
        json={"reason": "合规强撤"},
        headers=_headers(compliance_token),
    )
    assert forced.status_code == 200
    assert forced.json()["status"] == "withdrawn"


def test_revision_preview_carries_original_submission(app, client: TestClient):
    """needs_changes 后的新提交必须能通过预览指回原提交。"""
    _account(app, "contributor-5", (ROLE_CONTRIBUTOR,))
    _account(app, "domain-5", (ROLE_DOMAIN,))
    contributor_token = _login(client, "contributor-5")
    domain_token = _login(client, "domain-5")

    first = preview_card(_card("evc-service-5a"), contributor_id="local")
    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={"preview": first, "consent": True},
        headers=_headers(contributor_token),
    )
    original_id = uploaded.json()["evidence_id"]
    changed = client.post(
        f"/api/public/evidence/submissions/{original_id}/review",
        json={"action": ACTION_DOMAIN_NEEDS_CHANGES, "reason": "补充限制"},
        headers=_headers(domain_token),
    )
    assert changed.json()["status"] == "needs_changes"

    revised = preview_card(
        _card("evc-service-5b"),
        contributor_id="local",
        revises_submission_id=original_id,
    )
    assert revised["will_send"]["submission"]["revises_submission_id"] == original_id
    resubmitted = client.post(
        "/api/public/evidence/submissions",
        json={"preview": revised, "consent": True},
        headers=_headers(contributor_token),
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["status"] == "pending"


def test_build_envelope_rejects_preview_without_will_send():
    """缺 will_send 的预览必须是 ValueError，不能变成 500。"""
    preview = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "privacy": "passed",
        "will_send": None,
    }
    preview["preview_sha256"] = _payload_sha256(preview)
    with pytest.raises(ValueError, match="will_send"):
        build_envelope(preview, consent=True)


def test_login_rate_limit_and_generic_500(app, client: TestClient):
    """连续失败登录限速；未处理异常不向公网回传内部原文。"""
    _account(app, "limit-1", (ROLE_CONTRIBUTOR,))
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/login", json={"username": "limit-1", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"username": "limit-1", "password": "wrong"}
        ).status_code
        == 429
    )

    @app.get("/api/boom")
    def boom() -> None:
        """测试用未处理异常。"""
        raise RuntimeError("secret internal message")

    broken = TestClient(app, raise_server_exceptions=False)
    response = broken.get("/api/boom")
    assert response.status_code == 500
    assert "secret internal message" not in response.text


def test_auth_requires_valid_token_and_disabled_users_are_locked_out(app, client: TestClient):
    """登录失败、登出与禁用都必须即时生效。"""
    _account(app, "auth-1", (ROLE_CONTRIBUTOR,))
    _account(app, "disabled-1", (ROLE_CONTRIBUTOR,), disabled=True)

    assert (
        client.post(
            "/api/auth/login", json={"username": "auth-1", "password": "wrong-password"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": "disabled-1", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert client.get("/api/auth/me").status_code == 401

    token = _login(client, "auth-1")
    assert client.get("/api/auth/me", headers=_headers(token)).json()["user"]["roles"] == [
        ROLE_CONTRIBUTOR
    ]
    assert client.post("/api/auth/logout", headers=_headers(token)).status_code == 200
    assert client.get("/api/auth/me", headers=_headers(token)).status_code == 401


@pytest.mark.parametrize(
    ("username", "roles", "role", "capabilities", "can_contribute"),
    [
        (
            "principal-both",
            (ROLE_DOMAIN, ROLE_COMPLIANCE),
            ROLE_DOMAIN,
            (ACTION_DOMAIN_PASS, ACTION_DOMAIN_NEEDS_CHANGES, ACTION_DOMAIN_REJECT),
            False,
        ),
        (
            "principal-compliance",
            (ROLE_COMPLIANCE,),
            ROLE_COMPLIANCE,
            (
                ACTION_COMPLIANCE_PASS,
                ACTION_COMPLIANCE_NEEDS_CHANGES,
                ACTION_COMPLIANCE_REJECT,
            ),
            False,
        ),
        ("principal-none", (), None, (), False),
        ("principal-contributor", (ROLE_CONTRIBUTOR,), None, (), True),
    ],
)
def test_login_and_me_return_server_principal(
    app,
    client: TestClient,
    username: str,
    roles: tuple[str, ...],
    role: str | None,
    capabilities: tuple[str, ...],
    can_contribute: bool,
):
    """login 与 me 的 principal 一致：领域优先、仅合规、无角色、contributor。"""
    _account(app, username, roles)
    login = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert login.status_code == 200, login.text
    login_user = login.json()["user"]
    me_user = client.get("/api/auth/me", headers=_headers(login.json()["token"])).json()["user"]
    expected = {
        "role": role,
        "capabilities": list(capabilities),
        "can_review": role is not None,
        "can_contribute": can_contribute,
    }
    assert login_user["principal"] == expected
    assert me_user["principal"] == expected


def test_client_upload_and_citation_use_real_http_path(app, client: TestClient):
    """本地客户端封装走完整 HTTP 服务，而不是直接调用存储。"""
    _account(app, "client-1", (ROLE_CONTRIBUTOR,))
    _account(app, "domain-4", (ROLE_DOMAIN,))
    _account(app, "compliance-4", (ROLE_COMPLIANCE,))

    def handler(request: httpx.Request) -> httpx.Response:
        """把 httpx 请求转给 ASGI 测试客户端。"""
        response = client.request(
            request.method,
            str(request.url),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://public.test"
    )
    public = PublicEvidenceClient("http://public.test", http_client=http_client)
    try:
        logged_in = public.login("client-1", PASSWORD)
        assert logged_in["user"]["username"] == "client-1"
        preview = preview_card(_card("evc-service-4"), contributor_id="client-local")
        uploaded = public.upload(preview, consent=True)
        evidence_id = uploaded["evidence_id"]
        assert uploaded["contributor_id"] == logged_in["user"]["id"]

        evidence = app.state.governance.summary(evidence_id)
        app.state.governance.review(
            evidence_id,
            actor_id=app.state.accounts.get_user_by_username("domain-4")["id"],
            role=ROLE_DOMAIN,
            action=ACTION_DOMAIN_PASS,
            checklist=_checks(DOMAIN_CHECKLIST),
        )
        app.state.governance.review(
            evidence_id,
            actor_id=app.state.accounts.get_user_by_username("compliance-4")["id"],
            role=ROLE_COMPLIANCE,
            action=ACTION_COMPLIANCE_PASS,
            checklist=_checks(COMPLIANCE_CHECKLIST),
        )
        assert evidence["status"] == "pending"
        assert public.search("QC")[0]["evidence_id"] == evidence_id
        assert public.citation(evidence_id)["claim"]
        with pytest.raises(PublicEvidenceError) as missing:
            public.citation("evs-missing")
        assert missing.value.status == 404
        assert missing.value.code == "submission_not_found"
    finally:
        public.close()


def test_review_ui_is_served_and_queue_uses_governance_role(app, client: TestClient):
    """复核页可访问；页面只渲染服务端下发的动作规格与阻断文案。"""
    page = client.get("/review/")
    assert page.status_code == 200
    assert "公共证据" in page.text
    assert "登录" in page.text
    review_js = client.get("/review/review.js")
    assert review_js.status_code == 200
    assert 'blocked_code === "self_review"' not in review_js.text
    assert "requires_checklist" in review_js.text
    assert 'endsWith("_pass")' not in review_js.text

    _account(app, "ui-domain", (ROLE_DOMAIN,))
    queue = client.get(
        "/api/public/evidence/queue", headers=_headers(_login(client, "ui-domain"))
    ).json()
    assert queue["role"] == ROLE_DOMAIN


def test_missing_submission_returns_structured_code(client: TestClient):
    """缺失提交返回 404，并携带稳定 code。"""
    response = client.get("/api/public/evidence/evs-missing")
    assert response.status_code == 404
    assert response.json()["code"] == "submission_not_found"


def test_error_kinds_cover_validation_state_and_quota(app, client: TestClient):
    """校验失败、状态冲突与配额都带稳定 code。"""
    contributor = _account(app, "codes-contributor", (ROLE_CONTRIBUTOR,))
    token = _login(client, "codes-contributor")

    bad_card = _card("evc-codes-0")
    bad_card = bad_card.model_copy(
        update={
            "license": bad_card.license.model_copy(update={"attribution": r"C:\secret\raw.h5ad"})
        }
    )
    with pytest.raises(EvidenceError) as invalid:
        preview_card(bad_card, contributor_id=contributor)
    assert invalid.value.kind == "invalid_input"

    preview = preview_card(_card("evc-codes-1"), contributor_id=contributor)
    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={"preview": preview, "consent": True},
        headers=_headers(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    withdrawn = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/withdraw",
        json={"reason": "测试"},
        headers=_headers(token),
    )
    assert withdrawn.status_code == 409
    assert withdrawn.json()["code"] == "invalid_state"

    app.state.governance.max_daily_submissions = 0
    quota_preview = preview_card(_card("evc-codes-2"), contributor_id=contributor)
    quota = client.post(
        "/api/public/evidence/submissions",
        json={"preview": quota_preview, "consent": True},
        headers=_headers(token),
    )
    assert quota.status_code == 429
    assert quota.json()["code"] == "quota_exceeded"


def test_contributor_browser_preview_mine_and_withdraw(app, client: TestClient):
    """贡献者预览 → 上传 → 看状态/原因 → 修订 → 撤回。"""
    contributor = _account(app, "web-contributor", (ROLE_CONTRIBUTOR,))
    _account(app, "web-contributor-2", (ROLE_CONTRIBUTOR,))
    _account(app, "web-domain", (ROLE_DOMAIN,))
    _account(app, "web-compliance", (ROLE_COMPLIANCE,))
    token = _login(client, "web-contributor")
    domain_token = _login(client, "web-domain")
    compliance_token = _login(client, "web-compliance")

    card = _card("evc-web-1").model_dump(mode="json")
    previewed = client.post(
        "/api/public/evidence/preview", json={"card": card}, headers=_headers(token)
    )
    assert previewed.status_code == 200, previewed.text
    preview = previewed.json()
    assert preview["will_send"]["submission"]["contributor_id"] == contributor
    assert preview["admission"]["status"] == "passed"
    assert preview["not_sent"]
    assert (
        client.post(
            "/api/public/evidence/preview",
            json={"card": card},
            headers=_headers(domain_token),
        ).status_code
        == 403
    )

    tampered = {**preview, "privacy": "failed"}
    assert (
        client.post(
            "/api/public/evidence/submissions",
            json={"preview": tampered, "consent": True},
            headers=_headers(token),
        ).status_code
        == 400
    )
    uploaded = client.post(
        "/api/public/evidence/submissions",
        json={"preview": preview, "consent": True},
        headers=_headers(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]

    mine = client.get("/api/public/evidence/mine", headers=_headers(token)).json()["submissions"]
    assert [item["evidence_id"] for item in mine] == [evidence_id]
    assert mine[0]["status"] == "pending"
    assert mine[0]["latest_reason"] is None
    still_pending = client.post(
        "/api/public/evidence/preview",
        json={
            "card": _card("evc-web-1a").model_dump(mode="json"),
            "revises_submission_id": evidence_id,
        },
        headers=_headers(token),
    )
    assert still_pending.status_code == 409

    changed = client.post(
        f"/api/public/evidence/submissions/{evidence_id}/review",
        json={"action": ACTION_DOMAIN_NEEDS_CHANGES, "reason": "补充限制"},
        headers=_headers(domain_token),
    )
    assert changed.status_code == 200
    mine = client.get("/api/public/evidence/mine", headers=_headers(token)).json()["submissions"]
    assert mine[0]["latest_reason"] == "补充限制"
    other = client.post(
        "/api/public/evidence/preview",
        json={
            "card": _card("evc-web-1c").model_dump(mode="json"),
            "revises_submission_id": evidence_id,
        },
        headers=_headers(_login(client, "web-contributor-2")),
    )
    assert other.status_code == 403
    assert other.json()["code"] == "permission_denied"

    revised = client.post(
        "/api/public/evidence/preview",
        json={
            "card": _card("evc-web-1b").model_dump(mode="json"),
            "revises_submission_id": evidence_id,
        },
        headers=_headers(token),
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["will_send"]["submission"]["revises_submission_id"] == evidence_id
    resubmitted = client.post(
        "/api/public/evidence/submissions",
        json={"preview": revised.json(), "consent": True},
        headers=_headers(token),
    )
    assert resubmitted.status_code == 200, resubmitted.text
    revised_id = resubmitted.json()["evidence_id"]
    assert (
        client.post(
            f"/api/public/evidence/submissions/{revised_id}/review",
            json={"action": ACTION_DOMAIN_PASS, "checklist": _checks(DOMAIN_CHECKLIST)},
            headers=_headers(domain_token),
        ).json()["status"]
        == "pending"
    )
    assert (
        client.post(
            f"/api/public/evidence/submissions/{revised_id}/review",
            json={"action": ACTION_COMPLIANCE_PASS, "checklist": _checks(COMPLIANCE_CHECKLIST)},
            headers=_headers(compliance_token),
        ).json()["status"]
        == "accepted"
    )
    withdrawn = client.post(
        f"/api/public/evidence/submissions/{revised_id}/withdraw",
        json={"reason": "作者撤回"},
        headers=_headers(token),
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "withdrawn"


def test_reviewer_context_uses_domain_priority_and_rejects_no_role():
    """主复核角色领域优先；无复核角色沿用原 403 文案与类别。"""
    ctx = reviewer_context({"id": "acct-1", "roles": [ROLE_COMPLIANCE, ROLE_DOMAIN]})
    assert ctx == ReviewerContext(
        actor_id="acct-1",
        role=ROLE_DOMAIN,
        roles=(ROLE_COMPLIANCE, ROLE_DOMAIN),
    )

    with pytest.raises(EvidenceError) as denied:
        reviewer_context({"id": "acct-2", "roles": [ROLE_CONTRIBUTOR]})
    assert denied.value.kind == "permission_denied"
    assert str(denied.value) == "只有复核者可读取未发布全文"


class _StubService:
    """替换用的读服务桩；响应完全由桩决定并记录调用顺序。"""

    def __init__(self, store: GovernanceStore) -> None:
        """保存治理库并初始化调用记录。"""
        self.store = store
        self.calls: list[str] = []

    def queue(self, viewer: dict[str, Any]) -> dict[str, Any]:
        """返回固定队列并记录调用。"""
        self.calls.append("queue")
        return {"role": ROLE_DOMAIN, "queue": [{"evidence_id": "stub"}]}

    def events(self, evidence_id: str, viewer: dict[str, Any]) -> dict[str, Any]:
        """返回固定事件与能力并记录调用。"""
        self.calls.append(f"events:{evidence_id}")
        return {
            "events": [],
            "review": {
                "role": ROLE_COMPLIANCE,
                "actions": [],
                "blocked_reason": "桩",
                "blocked_code": "stub",
                "checklists": {},
            },
        }

    def citation(self, evidence_id: str) -> dict[str, Any]:
        """返回固定引用并记录调用。"""
        self.calls.append(f"citation:{evidence_id}")
        return {"evidence_id": evidence_id}


def test_public_evidence_service_is_replaceable(app, client: TestClient):
    """通过 app.state 工厂替换 service 后，路由经由工厂构造的实例取数。"""
    _account(app, "stub-domain", (ROLE_DOMAIN,))
    token = _login(client, "stub-domain")
    created: list[_StubService] = []

    def factory(store: GovernanceStore) -> _StubService:
        """记录被构造的 service，供测试断言调用顺序。"""
        service = _StubService(store)
        created.append(service)
        return service

    app.state.evidence_service_factory = factory

    queue = client.get("/api/public/evidence/queue", headers=_headers(token))
    assert queue.status_code == 200
    assert queue.json() == {"role": ROLE_DOMAIN, "queue": [{"evidence_id": "stub"}]}

    events = client.get("/api/public/evidence/evs-stub/events", headers=_headers(token))
    assert events.status_code == 200
    assert events.json()["review"]["blocked_code"] == "stub"
    assert [call for service in created for call in service.calls] == [
        "queue",
        "events:evs-stub",
    ]


def _service(tmp_path) -> PublicEvidenceService:
    """构造直接调用写命令的 service。"""
    return PublicEvidenceService(GovernanceStore(tmp_path / "public_evidence"))


CONTRIBUTOR = {"id": "acct-contributor", "roles": [ROLE_CONTRIBUTOR]}
OTHER_CONTRIBUTOR = {"id": "acct-contributor-2", "roles": [ROLE_CONTRIBUTOR]}
DOMAIN = {"id": "acct-domain", "roles": [ROLE_DOMAIN]}
COMPLIANCE = {"id": "acct-compliance", "roles": [ROLE_COMPLIANCE]}


def test_service_submit_overrides_contributor_and_requires_consent(tmp_path):
    """直接提交时用登录身份覆盖预览，未同意则拒绝。"""
    service = _service(tmp_path)
    preview = preview_card(_card("evc-svc-submit-1"), contributor_id="spoofed")

    submitted = service.submit(preview, True, CONTRIBUTOR)
    assert submitted["status"] == "pending"
    assert submitted["contributor_id"] == CONTRIBUTOR["id"]
    assert submitted["submitted_at"]

    with pytest.raises(EvidenceError) as denied:
        service.submit(preview, False, CONTRIBUTOR)
    assert denied.value.kind == "invalid_input"


def test_service_preview_checks_revision_ownership_and_state(tmp_path):
    """修订只能指向自己的 needs_changes 提交。"""
    service = _service(tmp_path)
    original = service.submit(
        service.preview(_card("evc-svc-preview-1"), CONTRIBUTOR), True, CONTRIBUTOR
    )
    evidence_id = original["evidence_id"]

    with pytest.raises(EvidenceError) as foreign:
        service.preview(_card("evc-svc-preview-2"), OTHER_CONTRIBUTOR, evidence_id)
    assert foreign.value.kind == "permission_denied"
    assert str(foreign.value) == "只能修订自己的提交"

    with pytest.raises(EvidenceError) as pending:
        service.preview(_card("evc-svc-preview-3"), CONTRIBUTOR, evidence_id)
    assert pending.value.kind == "invalid_state"
    assert str(pending.value) == "只能修订 needs_changes 的提交"

    service.review(
        evidence_id,
        ReviewRequest(action=ACTION_DOMAIN_NEEDS_CHANGES, reason="补充限制"),
        DOMAIN,
    )
    revised = service.preview(_card("evc-svc-preview-4"), CONTRIBUTOR, evidence_id)
    assert revised["will_send"]["submission"]["revises_submission_id"] == evidence_id
    assert revised["will_send"]["submission"]["contributor_id"] == CONTRIBUTOR["id"]


def test_service_review_checks_action_role_and_terminal_state(tmp_path):
    """未知动作、角色不符与已结束状态都在 service 内拒绝。"""
    service = _service(tmp_path)
    submitted = service.submit(
        service.preview(_card("evc-svc-review-1"), CONTRIBUTOR), True, CONTRIBUTOR
    )
    evidence_id = submitted["evidence_id"]

    with pytest.raises(EvidenceError) as unknown:
        service.review(evidence_id, ReviewRequest(action="bogus"), DOMAIN)
    assert unknown.value.kind == "invalid_input"
    assert str(unknown.value) == "未知审核动作"

    with pytest.raises(EvidenceError) as denied:
        service.review(
            evidence_id,
            ReviewRequest(action=ACTION_DOMAIN_PASS, checklist=_checks(DOMAIN_CHECKLIST)),
            CONTRIBUTOR,
        )
    assert denied.value.kind == "permission_denied"

    domain = service.review(
        evidence_id,
        ReviewRequest(action=ACTION_DOMAIN_PASS, checklist=_checks(DOMAIN_CHECKLIST)),
        DOMAIN,
    )
    assert domain["status"] == "pending"
    accepted = service.review(
        evidence_id,
        ReviewRequest(
            action=ACTION_COMPLIANCE_PASS,
            checklist=_checks(COMPLIANCE_CHECKLIST),
        ),
        COMPLIANCE,
    )
    assert accepted["status"] == "accepted"

    with pytest.raises(EvidenceError) as finished:
        service.review(
            evidence_id,
            ReviewRequest(action=ACTION_DOMAIN_PASS, checklist=_checks(DOMAIN_CHECKLIST)),
            DOMAIN,
        )
    assert finished.value.kind == "invalid_state"


def test_service_withdraw_checks_role_state_and_reason(tmp_path):
    """pending 不可撤、领域角色不可强撤，本人可撤回 accepted。"""
    service = _service(tmp_path)
    submitted = service.submit(
        service.preview(_card("evc-svc-withdraw-1"), CONTRIBUTOR), True, CONTRIBUTOR
    )
    evidence_id = submitted["evidence_id"]

    with pytest.raises(EvidenceError) as pending:
        service.withdraw(evidence_id, WithdrawRequest(reason="提前撤回"), CONTRIBUTOR)
    assert pending.value.kind == "invalid_state"

    for user, action, checklist in (
        (DOMAIN, ACTION_DOMAIN_PASS, DOMAIN_CHECKLIST),
        (COMPLIANCE, ACTION_COMPLIANCE_PASS, COMPLIANCE_CHECKLIST),
    ):
        service.review(
            evidence_id,
            ReviewRequest(action=action, checklist=_checks(checklist)),
            user,
        )

    with pytest.raises(EvidenceError) as denied:
        service.withdraw(evidence_id, WithdrawRequest(reason="越权"), DOMAIN)
    assert denied.value.kind == "permission_denied"
    assert str(denied.value) == "只能撤回自己的提交"

    withdrawn = service.withdraw(evidence_id, WithdrawRequest(reason="作者撤回"), CONTRIBUTOR)
    assert withdrawn["status"] == "withdrawn"
