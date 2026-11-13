"""公共服务雏形：认证、角色、上传、审核队列与引用索引。"""

from __future__ import annotations

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
from src.evidence.governance import (
    ACTION_COMPLIANCE_PASS,
    ACTION_DOMAIN_NEEDS_CHANGES,
    ACTION_DOMAIN_PASS,
    COMPLIANCE_CHECKLIST,
    DOMAIN_CHECKLIST,
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
)
from src.evidence.public_client import PublicEvidenceClient
from src.evidence.schema import (
    Claim,
    EvidenceCard,
    License,
    Method,
    Provenance,
    Review,
    Subject,
)
from src.public_api.app import create_public_app

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
    finally:
        public.close()


def test_review_ui_is_served_and_checklists_come_from_governance(app, client: TestClient):
    """复核页可访问；清单端点需登录且与治理层常量一致。"""
    page = client.get("/review/")
    assert page.status_code == 200
    assert "复核者登录" in page.text
    assert client.get("/review/review.js").status_code == 200

    assert client.get("/api/public/evidence/checklists").status_code == 401
    _account(app, "ui-contributor", (ROLE_CONTRIBUTOR,))
    _account(app, "ui-domain", (ROLE_DOMAIN,))
    token = _login(client, "ui-contributor")
    assert client.get("/api/public/evidence/checklists", headers=_headers(token)).status_code == 403
    data = client.get(
        "/api/public/evidence/checklists", headers=_headers(_login(client, "ui-domain"))
    ).json()
    assert data[ROLE_DOMAIN] == list(DOMAIN_CHECKLIST)
    assert data[ROLE_COMPLIANCE] == list(COMPLIANCE_CHECKLIST)
    queue = client.get(
        "/api/public/evidence/queue", headers=_headers(_login(client, "ui-domain"))
    ).json()
    assert queue["role"] == ROLE_DOMAIN
