"""公共证据部署运维：审计导出与备份恢复。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.cli import evidence_backup, evidence_governance
from src.evidence.accounts import AccountStore, hash_password
from src.evidence.contribution import build_envelope, preview_card
from src.evidence.governance import (
    ACTION_COMPLIANCE_PASS,
    ACTION_DOMAIN_PASS,
    COMPLIANCE_CHECKLIST,
    DOMAIN_CHECKLIST,
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    GovernanceStore,
    public_evidence_dir,
)
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


def _card(card_id: str = "evc-deploy-1") -> EvidenceCard:
    """构造一张通过隐私与准入检查的最小证据卡。"""
    return EvidenceCard(
        card_id=card_id,
        created_at="2026-11-13T00:00:00+00:00",
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


def _store(root: Path) -> GovernanceStore:
    """绑定测试数据根的治理库。"""
    return GovernanceStore(public_evidence_dir(root))


def _submit(store: GovernanceStore, card: EvidenceCard) -> dict:
    """预览并提交一张证据卡。"""
    preview = preview_card(card, contributor_id="contributor-1")
    return store.submit(build_envelope(preview, consent=True))


def _accept(store: GovernanceStore, evidence_id: str) -> dict:
    """领域与合规双签，返回 accepted 摘要。"""
    store.review(
        evidence_id,
        actor_id="domain-1",
        role=ROLE_DOMAIN,
        action=ACTION_DOMAIN_PASS,
        checklist={name: True for name in DOMAIN_CHECKLIST},
    )
    return store.review(
        evidence_id,
        actor_id="compliance-1",
        role=ROLE_COMPLIANCE,
        action=ACTION_COMPLIANCE_PASS,
        checklist={name: True for name in COMPLIANCE_CHECKLIST},
    )


def test_audit_export_matches_db_and_excludes_accounts(tmp_path):
    """审计 JSONL 条数与治理库一致，且不包含账号库内容。"""
    store = _store(tmp_path)
    evidence = _submit(store, _card())
    _accept(store, evidence["evidence_id"])
    store.withdraw(
        evidence["evidence_id"],
        actor_id="contributor-1",
        role=ROLE_CONTRIBUTOR,
        reason="发现方法错误",
    )
    accounts = AccountStore(public_evidence_dir(tmp_path))
    accounts.create_user("alice", hash_password(PASSWORD), roles=(ROLE_CONTRIBUTOR,))

    out = tmp_path / "audit.jsonl"
    assert evidence_governance.main(["--root", str(tmp_path), "audit", "--out", str(out)]) == 0
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    with sqlite3.connect(public_evidence_dir(tmp_path) / "governance.db") as conn:
        submissions = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert sum(record["type"] == "submission" for record in records) == submissions
    assert sum(record["type"] == "event" for record in records) == events
    assert len(records) == submissions + events
    assert all("submission_id" in record for record in records if record["type"] == "event")
    assert records[0]["card"]["card_id"] == "evc-deploy-1"

    text = out.read_text(encoding="utf-8")
    assert "password_hash" not in text
    assert "$argon2" not in text


def test_backup_restore_preserves_accounts_and_accepted_evidence(tmp_path):
    """备份到新目录再恢复后，账号、检索、引用与事件仍可用。"""
    source = tmp_path / "source"
    store = _store(source)
    evidence = _accept(store, _submit(store, _card())["evidence_id"])
    assert evidence["status"] == "accepted"
    accounts = AccountStore(public_evidence_dir(source))
    accounts.create_user("alice", hash_password(PASSWORD), roles=(ROLE_CONTRIBUTOR,))
    accounts.create_user("domain-1", hash_password(PASSWORD), roles=(ROLE_DOMAIN,))

    backup_dir = tmp_path / "backup"
    assert evidence_backup.main(["--root", str(source), "backup", "--out", str(backup_dir)]) == 0
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cards"] == 1
    assert set(manifest["databases"]) == {"governance.db", "accounts.db"}

    restored = tmp_path / "restored"
    assert (
        evidence_backup.main(["--root", str(restored), "restore", "--backup", str(backup_dir)]) == 0
    )
    client = TestClient(create_public_app(restored))
    login = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert client.get("/api/public/evidence/search?q=QC").json()["count"] == 1
    assert client.get(f"/api/public/evidence/{evidence['evidence_id']}/citation").status_code == 200

    reviewer = client.post("/api/auth/login", json={"username": "domain-1", "password": PASSWORD})
    assert reviewer.status_code == 200, reviewer.text
    events = client.get(
        f"/api/public/evidence/{evidence['evidence_id']}/events",
        headers={"Authorization": f"Bearer {reviewer.json()['token']}"},
    )
    assert events.status_code == 200, events.text
    assert len(events.json()["events"]) == 3


def test_restore_refuses_non_empty_target(tmp_path):
    """恢复目标已有内容时拒绝，避免覆盖线上数据。"""
    source = tmp_path / "source"
    _accept(_store(source), _submit(_store(source), _card())["evidence_id"])
    AccountStore(public_evidence_dir(source)).create_user(
        "alice", hash_password(PASSWORD), roles=(ROLE_CONTRIBUTOR,)
    )
    backup_dir = tmp_path / "backup"
    assert evidence_backup.main(["--root", str(source), "backup", "--out", str(backup_dir)]) == 0

    target = tmp_path / "target"
    (target / "public_evidence").mkdir(parents=True)
    (target / "public_evidence" / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit, match="必须为空"):
        evidence_backup.main(["--root", str(target), "restore", "--backup", str(backup_dir)])


def test_restore_rejects_tampered_database(tmp_path):
    """备份数据库哈希不符时拒绝恢复。"""
    source = tmp_path / "source"
    _accept(_store(source), _submit(_store(source), _card())["evidence_id"])
    AccountStore(public_evidence_dir(source)).create_user(
        "alice", hash_password(PASSWORD), roles=(ROLE_CONTRIBUTOR,)
    )
    backup_dir = tmp_path / "backup"
    assert evidence_backup.main(["--root", str(source), "backup", "--out", str(backup_dir)]) == 0
    (backup_dir / "governance.db").write_bytes(b"broken")

    with pytest.raises(SystemExit, match="校验失败"):
        evidence_backup.main(
            ["--root", str(tmp_path / "restored"), "restore", "--backup", str(backup_dir)]
        )


def test_restore_rejects_tampered_card(tmp_path):
    """备份卡内容被改动时拒绝恢复。"""
    source = tmp_path / "source"
    _accept(_store(source), _submit(_store(source), _card())["evidence_id"])
    AccountStore(public_evidence_dir(source)).create_user(
        "alice", hash_password(PASSWORD), roles=(ROLE_CONTRIBUTOR,)
    )
    backup_dir = tmp_path / "backup"
    assert evidence_backup.main(["--root", str(source), "backup", "--out", str(backup_dir)]) == 0
    (backup_dir / "cards" / "evc-deploy-1.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="卡内容校验失败"):
        evidence_backup.main(
            ["--root", str(tmp_path / "restored"), "restore", "--backup", str(backup_dir)]
        )


def test_trusted_proxy_uses_last_forwarded_for(monkeypatch, tmp_path):
    """信任代理时只信 X-Forwarded-For 最后一段，忽略 X-Real-IP。"""
    monkeypatch.setenv("CELLSPATIO_TRUST_PROXY", "1")
    client = TestClient(create_public_app(tmp_path))
    username = f"proxy-{uuid.uuid4().hex}"
    base = {
        "X-Forwarded-For": "198.51.100.1, 203.0.113.7",
        "Content-Type": "application/json",
    }
    for attempt in range(5):
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": "wrong"},
            headers={**base, "X-Real-IP": f"192.0.2.{attempt + 1}"},
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/auth/login",
        json={"username": username, "password": "wrong"},
        headers={**base, "X-Real-IP": "192.0.2.200"},
    )
    assert blocked.status_code == 429
