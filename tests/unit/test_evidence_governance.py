"""公共证据治理：准入、状态机、撤销与检索。"""

from __future__ import annotations

import json

import pytest

from src.cli import evidence_governance
from src.evidence.governance import (
    ACTION_COMPLIANCE_NEEDS_CHANGES,
    ACTION_COMPLIANCE_PASS,
    ACTION_DOMAIN_PASS,
    ACTION_DOMAIN_REJECT,
    COMPLIANCE_CHECKLIST,
    CONSENT_TEXT_VERSION,
    DOMAIN_CHECKLIST,
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    GovernanceStore,
    SubmissionEnvelope,
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


def _card(
    card_id: str = "evc-1",
    *,
    claim: str = "QC 阈值经人工批准",
    gate: str = "qc_review",
    outcome: str = "accepted",
    source_ids: list[str] | None = None,
) -> EvidenceCard:
    """构造一张通过隐私扫描的最小证据卡。"""
    return EvidenceCard(
        card_id=card_id,
        created_at="2026-11-12T00:00:00+00:00",
        gate=gate,
        outcome=outcome,
        subject=Subject(session_id="s", run_id="r", binding_sha256="a" * 64),
        method=Method(tool="sc_qc", tool_version="1", parameters={"min_features": 200}),
        observation={"cells": 10},
        claim=Claim(text=claim),
        limitations=["探索性结果"],
        provenance=Provenance(source_ids=source_ids or []),
        review=Review(status=outcome),
        license=License(spdx="CC-BY-4.0", attribution="tester"),
    )


def _envelope(card: EvidenceCard, contributor: str = "alice", **kwargs) -> SubmissionEnvelope:
    """构造合规提交信封。"""
    return SubmissionEnvelope(
        contributor_id=contributor,
        card=card,
        consent=True,
        consent_text_version=CONSENT_TEXT_VERSION,
        **kwargs,
    )


def _checks(names: tuple[str, ...]) -> dict[str, bool]:
    """把清单名全部置 true。"""
    return {name: True for name in names}


def _accept(store: GovernanceStore, submission_id: str) -> dict:
    """走完领域与合规双签。"""
    store.review(
        submission_id,
        actor_id="dora",
        role=ROLE_DOMAIN,
        action=ACTION_DOMAIN_PASS,
        checklist=_checks(DOMAIN_CHECKLIST),
    )
    return store.review(
        submission_id,
        actor_id="carl",
        role=ROLE_COMPLIANCE,
        action=ACTION_COMPLIANCE_PASS,
        checklist=_checks(COMPLIANCE_CHECKLIST),
    )


def test_submit_and_dual_sign_make_accepted(tmp_path):
    store = GovernanceStore(tmp_path)
    submitted = store.submit(_envelope(_card()))

    assert submitted["status"] == "pending"
    accepted = _accept(store, submitted["evidence_id"])

    assert accepted["status"] == "accepted"
    assert accepted["accepted_at"]
    results = store.search()
    assert [item["evidence_id"] for item in results] == [submitted["evidence_id"]]
    assert results[0]["evidence_id"] == submitted["evidence_id"]


def test_contributor_cannot_review_own_submission(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card(), contributor="alice"))["evidence_id"]

    with pytest.raises(ValueError, match="不得审核自己"):
        store.review(
            sid,
            actor_id="alice",
            role=ROLE_DOMAIN,
            action=ACTION_DOMAIN_PASS,
            checklist=_checks(DOMAIN_CHECKLIST),
        )


def test_same_actor_cannot_sign_both_roles(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card()))["evidence_id"]
    store.review(
        sid,
        actor_id="same",
        role=ROLE_DOMAIN,
        action=ACTION_DOMAIN_PASS,
        checklist=_checks(DOMAIN_CHECKLIST),
    )

    with pytest.raises(ValueError, match="不能同时签署"):
        store.review(
            sid,
            actor_id="same",
            role=ROLE_COMPLIANCE,
            action=ACTION_COMPLIANCE_PASS,
            checklist=_checks(COMPLIANCE_CHECKLIST),
        )


def test_pass_requires_full_checklist(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card()))["evidence_id"]

    with pytest.raises(ValueError, match="未全部通过"):
        store.review(
            sid,
            actor_id="dora",
            role=ROLE_DOMAIN,
            action=ACTION_DOMAIN_PASS,
            checklist={"method_complete": True},
        )


def test_needs_changes_reason_and_revision(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card()))["evidence_id"]

    with pytest.raises(ValueError, match="必须填写原因"):
        store.review(
            sid,
            actor_id="carl",
            role=ROLE_COMPLIANCE,
            action=ACTION_COMPLIANCE_NEEDS_CHANGES,
        )

    changed = store.review(
        sid,
        actor_id="carl",
        role=ROLE_COMPLIANCE,
        action=ACTION_COMPLIANCE_NEEDS_CHANGES,
        reason="补充许可说明",
    )
    assert changed["status"] == "needs_changes"
    with pytest.raises(ValueError, match="只有 pending"):
        store.review(
            sid,
            actor_id="dora",
            role=ROLE_DOMAIN,
            action=ACTION_DOMAIN_PASS,
            checklist=_checks(DOMAIN_CHECKLIST),
        )

    revision = store.submit(
        _envelope(_card("evc-2", claim="QC 阈值经人工批准（补充许可）"), revises_submission_id=sid)
    )
    assert revision["status"] == "pending"
    assert revision["evidence_id"] != sid
    assert store.summary(sid)["status"] == "needs_changes"


def test_only_needs_changes_can_be_revised(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card("evc-a")))["evidence_id"]
    _accept(store, sid)

    with pytest.raises(ValueError, match="只能修订 needs_changes"):
        store.submit(
            _envelope(
                _card("evc-b", claim="补充说明"),
                revises_submission_id=sid,
            )
        )


def test_rejection_requires_reason_and_is_not_searchable(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card()))["evidence_id"]
    result = store.review(
        sid,
        actor_id="dora",
        role=ROLE_DOMAIN,
        action=ACTION_DOMAIN_REJECT,
        reason="方法描述不足",
    )

    assert result["status"] == "rejected"
    assert store.search() == []
    tombstone = store.get(sid)
    assert tombstone["status"] == "rejected"
    assert tombstone["reason"] == "方法描述不足"
    assert "card" not in tombstone


def test_submission_requires_license(tmp_path):
    store = GovernanceStore(tmp_path)
    card = _card()
    card.license = License()

    with pytest.raises(ValueError, match="license.spdx"):
        store.submit(_envelope(card))


def test_submission_requires_current_card_schema(tmp_path):
    store = GovernanceStore(tmp_path)
    card = _card()
    card.schema_version = "cellspatio-evidence-card/0.9.0"

    with pytest.raises(ValueError, match="证据卡 schema_version"):
        store.submit(_envelope(card))


def test_duplicate_card_id_rejected(tmp_path):
    store = GovernanceStore(tmp_path)
    store.submit(_envelope(_card()))

    with pytest.raises(ValueError, match="同一 card_id"):
        store.submit(_envelope(_card(), contributor="bob"))


def test_qa_card_requires_valid_source_id(tmp_path):
    store = GovernanceStore(tmp_path)

    with pytest.raises(ValueError, match="至少有一个来源"):
        store.submit(_envelope(_card(gate="knowledge_qa_review")))
    with pytest.raises(ValueError, match="来源 ID 格式"):
        store.submit(
            _envelope(_card("evc-qa", gate="knowledge_qa_review", source_ids=["not-a-source"]))
        )


def test_withdraw_hides_from_search_and_returns_tombstone(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card()))["evidence_id"]
    _accept(store, sid)
    store.withdraw(sid, actor_id="alice", role=ROLE_CONTRIBUTOR, reason="发现方法错误")

    assert store.search() == []
    tombstone = store.get(sid)
    assert tombstone["status"] == "withdrawn"
    assert tombstone["reason"] == "发现方法错误"
    assert "card" not in tombstone


def test_supersedes_marks_old_and_restores_after_withdrawal(tmp_path):
    store = GovernanceStore(tmp_path)
    first = store.submit(_envelope(_card("evc-a", claim="旧结论")))["evidence_id"]
    _accept(store, first)
    second = store.submit(_envelope(_card("evc-b", claim="新结论"), supersedes=[first]))[
        "evidence_id"
    ]
    _accept(store, second)

    results = store.search()
    assert [item["evidence_id"] for item in results] == [second, first]
    assert results[1]["superseded_by"] == second

    store.withdraw(second, actor_id="carl", role=ROLE_COMPLIANCE, reason="新结论有误")
    restored = store.search()
    assert [item["evidence_id"] for item in restored] == [first]
    assert restored[0]["superseded_by"] is None


def test_supersedes_unknown_or_rejected_target(tmp_path):
    store = GovernanceStore(tmp_path)

    with pytest.raises(ValueError, match="不存在"):
        store.submit(_envelope(_card("evc-b"), supersedes=["evs-missing"]))

    pending = store.submit(_envelope(_card("evc-c")))["evidence_id"]
    with pytest.raises(ValueError, match="只能取代 accepted"):
        store.submit(_envelope(_card("evc-d"), supersedes=[pending]))


def test_supersedes_cannot_point_to_self(tmp_path):
    store = GovernanceStore(tmp_path)

    with pytest.raises(ValueError, match="不能指向自己"):
        store.submit(
            _envelope(_card("evc-self"), submission_id="evs-self", supersedes=["evs-self"])
        )


def test_submission_limits(tmp_path):
    store = GovernanceStore(tmp_path, max_daily_submissions=1, max_open_submissions=5)
    store.submit(_envelope(_card("evc-1")))

    with pytest.raises(ValueError, match="每日提交限额"):
        store.submit(_envelope(_card("evc-2"), contributor="alice"))


def test_open_limit_counts_accepted_until_withdrawn(tmp_path):
    store = GovernanceStore(tmp_path, max_open_submissions=1)
    sid = store.submit(_envelope(_card("evc-1")))["evidence_id"]
    _accept(store, sid)

    with pytest.raises(ValueError, match="未终态提交数超过限额"):
        store.submit(_envelope(_card("evc-2")))

    store.withdraw(sid, actor_id="alice", role=ROLE_CONTRIBUTOR, reason="替换为新证据")
    assert store.submit(_envelope(_card("evc-2")))["status"] == "pending"


def test_reviewer_view_reads_pending_card(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card()))["evidence_id"]

    assert "card" not in store.get(sid)
    assert store.summary(sid)["claim"] is None
    with pytest.raises(ValueError, match="只有复核者"):
        store.get(sid, include_unaccepted=True)
    with pytest.raises(ValueError, match="贡献者不得读取"):
        store.get(sid, include_unaccepted=True, viewer_actor="alice", viewer_role=ROLE_DOMAIN)
    assert (
        store.get(
            sid,
            include_unaccepted=True,
            viewer_actor="dora",
            viewer_role=ROLE_DOMAIN,
        )["card"]["card_id"]
        == "evc-1"
    )


def test_submitted_at_is_preserved(tmp_path):
    store = GovernanceStore(tmp_path)
    submitted = store.submit(_envelope(_card(), submitted_at="2026-11-01T08:00:00+00:00"))

    assert submitted["submitted_at"] == "2026-11-01T08:00:00+00:00"
    with pytest.raises(ValueError, match="ISO 8601"):
        store.submit(_envelope(_card("evc-2"), submitted_at="不是时间"))


def test_only_contributor_or_compliance_can_withdraw(tmp_path):
    store = GovernanceStore(tmp_path)
    sid = store.submit(_envelope(_card()))["evidence_id"]
    _accept(store, sid)

    with pytest.raises(ValueError, match="只有贡献者或合规"):
        store.withdraw(sid, actor_id="dora", role=ROLE_DOMAIN, reason="越权")
    result = store.withdraw(sid, actor_id="carl", role=ROLE_COMPLIANCE, reason="合规强撤")
    assert result["status"] == "withdrawn"


def test_cli_submit_review_search(tmp_path, capsys):
    envelope = _envelope(_card()).model_dump(mode="json")
    path = tmp_path / "envelope.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    assert evidence_governance.main(["--root", str(tmp_path), "submit", str(path)]) == 0
    sid = json.loads(capsys.readouterr().out)["evidence_id"]

    common = ["--root", str(tmp_path), "review", sid]
    assert (
        evidence_governance.main(
            [
                *common,
                "--actor",
                "dora",
                "--role",
                ROLE_DOMAIN,
                "--action",
                ACTION_DOMAIN_PASS,
                "--check",
                "method_complete=true",
                "--check",
                "claim_matches_observation=true",
                "--check",
                "limitations_present=true",
            ]
        )
        == 0
    )
    assert (
        evidence_governance.main(
            [
                *common,
                "--actor",
                "carl",
                "--role",
                ROLE_COMPLIANCE,
                "--action",
                ACTION_COMPLIANCE_PASS,
                "--check",
                "privacy_scan_passed=true",
                "--check",
                "license_and_attribution_present=true",
                "--check",
                "no_raw_data=true",
                "--check",
                "small_sample_ok=true",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert evidence_governance.main(["--root", str(tmp_path), "search"]) == 0
    results = json.loads(capsys.readouterr().out)
    assert [item["evidence_id"] for item in results] == [sid]
