"""公共证据复核协议：纯规则单测。"""

from __future__ import annotations

import pytest

from src.evidence.errors import EvidenceError
from src.evidence.review_protocol import (
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    action_role,
    checklist_for_role,
    next_state,
    queue_eligible,
    reviewer_role,
    viewer_access,
    withdraw_role,
)


def test_action_role_mapping_is_fixed():
    """动作到角色的映射与现有治理层一致。"""
    assert action_role("domain_pass") == ROLE_DOMAIN
    assert action_role("domain_needs_changes") == ROLE_DOMAIN
    assert action_role("domain_reject") == ROLE_DOMAIN
    assert action_role("compliance_pass") == ROLE_COMPLIANCE
    assert action_role("compliance_needs_changes") == ROLE_COMPLIANCE
    assert action_role("compliance_reject") == ROLE_COMPLIANCE
    assert action_role("submit") is None
    assert action_role("withdraw") is None


def test_checklist_for_role_returns_existing_keys():
    """清单键保持现有顺序与字面内容。"""
    assert checklist_for_role(ROLE_DOMAIN) == (
        "method_complete",
        "claim_matches_observation",
        "limitations_present",
    )
    assert checklist_for_role(ROLE_COMPLIANCE) == (
        "privacy_scan_passed",
        "license_and_attribution_present",
        "no_raw_data",
        "small_sample_ok",
    )
    assert checklist_for_role(ROLE_CONTRIBUTOR) == ()


@pytest.mark.parametrize(
    ("action", "prior_actions", "expected"),
    [
        ("domain_reject", (), "rejected"),
        ("compliance_reject", ("domain_pass",), "rejected"),
        ("domain_needs_changes", (), "needs_changes"),
        ("compliance_needs_changes", ("domain_pass",), "needs_changes"),
        ("domain_pass", (), "pending"),
        ("compliance_pass", (), "pending"),
        ("domain_pass", ("compliance_pass",), "accepted"),
        ("compliance_pass", ("domain_pass",), "accepted"),
        ("domain_pass", ("domain_pass",), "pending"),
        ("withdraw", ("domain_pass", "compliance_pass"), "withdrawn"),
    ],
)
def test_next_state_matches_existing_transitions(
    action: str, prior_actions: tuple[str, ...], expected: str
):
    """拒绝/补充优先，双签通过才 accepted。"""
    assert next_state(action, prior_actions) == expected


def test_queue_eligible_matches_review_queue_predicate():
    """队列只要 pending、非本人提交、本人未审且本角色未签。"""
    base = {
        "status": "pending",
        "contributor_id": "alice",
        "viewer_actor": "dora",
        "viewer_role": ROLE_DOMAIN,
    }
    assert queue_eligible(**base, events=()) is True
    assert queue_eligible(**{**base, "contributor_id": "dora"}, events=()) is False
    assert queue_eligible(**{**base, "status": "accepted"}, events=()) is False
    assert queue_eligible(**{**base, "viewer_role": ROLE_COMPLIANCE}, events=()) is True
    assert queue_eligible(**base, events=(("dora", "domain_needs_changes"),)) is False
    assert queue_eligible(**base, events=(("dora", "domain_pass"),)) is False
    assert queue_eligible(**base, events=(("sam", "domain_pass"),)) is False
    assert (
        queue_eligible(**{**base, "viewer_role": ROLE_COMPLIANCE}, events=(("sam", "domain_pass"),))
        is True
    )
    assert (
        queue_eligible(
            **{**base, "viewer_role": ROLE_COMPLIANCE}, events=(("sam", "compliance_pass"),)
        )
        is False
    )
    assert queue_eligible(**{**base, "viewer_role": ROLE_CONTRIBUTOR}, events=()) is False


def test_viewer_access_returns_role_specific_actions():
    """待审且未签时，只返回本角色的三个动作。"""
    access = viewer_access(
        status="pending",
        contributor_id="alice",
        viewer_actor="dora",
        viewer_role=ROLE_DOMAIN,
        events=(),
    )
    assert access.actions == ("domain_pass", "domain_needs_changes", "domain_reject")
    assert access.blocked_reason is None

    compliance = viewer_access(
        status="pending",
        contributor_id="alice",
        viewer_actor="cara",
        viewer_role=ROLE_COMPLIANCE,
        events=(),
    )
    assert compliance.actions == (
        "compliance_pass",
        "compliance_needs_changes",
        "compliance_reject",
    )
    assert compliance.blocked_reason is None


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"viewer_role": ROLE_CONTRIBUTOR}, "当前账号没有复核角色。"),
        ({"status": "accepted"}, "该提交已结束审核。"),
        ({"contributor_id": "dora"}, "贡献者不得审核自己的提交"),
        ({"events": (("sam", "domain_pass"),)}, "本角色已签署，等待另一角色复核。"),
        ({"events": (("dora", "domain_pass"),)}, "本角色已签署，等待另一角色复核。"),
        ({"events": (("dora", "compliance_pass"),)}, "你已复核过该提交。"),
    ],
)
def test_viewer_access_blocked_reasons(kwargs, reason):
    """阻断原因按现有页面顺序：角色、状态、自审、本角色已签、本人已审。"""
    base = {
        "status": "pending",
        "contributor_id": "alice",
        "viewer_actor": "dora",
        "viewer_role": ROLE_DOMAIN,
        "events": (),
    }
    access = viewer_access(**{**base, **kwargs})
    assert access.actions == ()
    assert access.blocked_reason == reason


def test_reviewer_role_prefers_domain():
    """双角色账号优先按领域复核者展示。"""
    assert reviewer_role((ROLE_COMPLIANCE, ROLE_DOMAIN)) == ROLE_DOMAIN
    assert reviewer_role((ROLE_DOMAIN, ROLE_COMPLIANCE)) == ROLE_DOMAIN
    assert reviewer_role((ROLE_COMPLIANCE,)) == ROLE_COMPLIANCE
    assert reviewer_role((ROLE_CONTRIBUTOR,)) is None
    assert reviewer_role(()) is None


def test_withdraw_role_derivation():
    """本人优先按贡献者撤销；否则只有合规角色可强制撤销。"""
    assert (
        withdraw_role(actor_id="alice", contributor_id="alice", roles=(ROLE_COMPLIANCE,))
        == ROLE_CONTRIBUTOR
    )
    assert (
        withdraw_role(actor_id="cara", contributor_id="alice", roles=(ROLE_COMPLIANCE,))
        == ROLE_COMPLIANCE
    )
    with pytest.raises(EvidenceError) as denied:
        withdraw_role(actor_id="cara", contributor_id="alice", roles=(ROLE_DOMAIN,))
    assert denied.value.kind == "permission_denied"
    assert "只能撤回自己的提交" in str(denied.value)
