"""公共证据复核协议：纯规则单测。"""

from __future__ import annotations

import pytest

from src.evidence.errors import EvidenceError
from src.evidence.review_protocol import (
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    action_kind,
    action_role,
    action_spec,
    checklist_for_role,
    next_state,
    queue_eligible,
    review_decision,
    reviewer_role,
    viewer_access,
    withdraw_decision,
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


def test_action_spec_describes_kind_and_required_inputs():
    """复核动作由服务端下发类别与必填项，界面不再按名称推断。"""
    assert action_kind("domain_pass") == "pass"
    assert action_kind("compliance_pass") == "pass"
    assert action_kind("domain_needs_changes") == "needs_changes"
    assert action_kind("compliance_reject") == "reject"
    assert action_spec("domain_pass") == {
        "id": "domain_pass",
        "kind": "pass",
        "requires_checklist": True,
        "requires_reason": False,
    }
    assert action_spec("compliance_needs_changes") == {
        "id": "compliance_needs_changes",
        "kind": "needs_changes",
        "requires_checklist": False,
        "requires_reason": True,
    }
    assert action_spec("domain_reject")["requires_reason"] is True
    with pytest.raises(EvidenceError) as unknown:
        action_spec("submit")
    assert unknown.value.kind == "invalid_input"


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
        viewer_roles=(ROLE_DOMAIN,),
        events=(),
    )
    assert access.actions == ("domain_pass", "domain_needs_changes", "domain_reject")
    assert access.blocked_reason is None
    assert access.role == ROLE_DOMAIN

    compliance = viewer_access(
        status="pending",
        contributor_id="alice",
        viewer_actor="cara",
        viewer_roles=(ROLE_COMPLIANCE,),
        events=(),
    )
    assert compliance.actions == (
        "compliance_pass",
        "compliance_needs_changes",
        "compliance_reject",
    )
    assert compliance.blocked_reason is None
    assert compliance.role == ROLE_COMPLIANCE


def test_dual_role_reviewer_signs_remaining_role():
    """同一账号持双角色时，签完领域后可继续签合规。"""
    access = viewer_access(
        status="pending",
        contributor_id="alice",
        viewer_actor="dora",
        viewer_roles=(ROLE_DOMAIN, ROLE_COMPLIANCE),
        events=(("dora", "domain_pass"),),
    )
    assert access.actions == (
        "compliance_pass",
        "compliance_needs_changes",
        "compliance_reject",
    )
    assert access.role == ROLE_COMPLIANCE
    assert access.blocked_reason is None


@pytest.mark.parametrize(
    ("kwargs", "reason", "code"),
    [
        ({"viewer_roles": (ROLE_CONTRIBUTOR,)}, "当前账号没有复核角色。", "no_role"),
        ({"status": "accepted"}, "该提交已结束审核。", "finished"),
        ({"contributor_id": "dora"}, "贡献者不得审核自己的提交", "self_review"),
        (
            {"viewer_roles": (ROLE_DOMAIN,), "events": (("sam", "domain_pass"),)},
            "本角色已签署，等待另一角色复核。",
            "role_signed",
        ),
        (
            {"viewer_roles": (ROLE_DOMAIN,), "events": (("dora", "domain_pass"),)},
            "本角色已签署，等待另一角色复核。",
            "role_signed",
        ),
    ],
)
def test_viewer_access_blocked_reasons(kwargs, reason, code):
    """阻断原因顺序：角色、状态、自审、本角色已签。"""
    base = {
        "status": "pending",
        "contributor_id": "alice",
        "viewer_actor": "dora",
        "viewer_roles": (ROLE_DOMAIN,),
        "events": (),
    }
    access = viewer_access(**{**base, **kwargs})
    assert access.actions == ()
    assert access.blocked_reason == reason
    assert access.blocked_code == code


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


def test_withdraw_decision_keeps_existing_errors_and_transition():
    """撤回校验只在协议内：本人或合规强撤，状态与角色错误沿用现有文案。"""
    base = {
        "status": "accepted",
        "contributor_id": "alice",
        "actor_id": "alice",
        "role": ROLE_CONTRIBUTOR,
        "reason": "发现方法错误",
    }
    assert withdraw_decision(**base) == "withdrawn"
    assert withdraw_decision(**{**base, "actor_id": "carl", "role": ROLE_COMPLIANCE}) == "withdrawn"

    cases = [
        ({"status": "pending"}, "invalid_state", "只有 accepted 提交可撤销"),
        ({"actor_id": "carl", "role": ROLE_CONTRIBUTOR}, "permission_denied", "只能撤回自己的提交"),
        ({"actor_id": "dora", "role": ROLE_DOMAIN}, "permission_denied", "只有贡献者或合规"),
        ({"reason": "  "}, "invalid_input", "撤销必须填写原因"),
        ({"actor_id": " "}, "invalid_input", "actor_id 必填"),
    ]
    for overrides, kind, message in cases:
        with pytest.raises(EvidenceError) as blocked:
            withdraw_decision(**{**base, **overrides})
        assert blocked.value.kind == kind
        assert message in str(blocked.value)


def _checks(role: str) -> dict[str, bool]:
    """该角色清单全部通过。"""
    return {name: True for name in checklist_for_role(role)}


def test_review_decision_returns_next_state():
    """纯规则校验通过时给出与状态机一致的新状态。"""
    base = {
        "status": "pending",
        "contributor_id": "alice",
        "actor_id": "dora",
        "role": ROLE_DOMAIN,
        "action": "domain_pass",
        "checklist": _checks(ROLE_DOMAIN),
        "reason": None,
        "events": (),
    }
    assert review_decision(**base) == "pending"
    assert (
        review_decision(
            **{
                **base,
                "actor_id": "cara",
                "role": ROLE_COMPLIANCE,
                "action": "compliance_pass",
                "checklist": _checks(ROLE_COMPLIANCE),
                "events": (("dora", "domain_pass"),),
            }
        )
        == "accepted"
    )


@pytest.mark.parametrize(
    ("overrides", "kind", "message"),
    [
        ({"status": "accepted"}, "invalid_state", "只有 pending"),
        ({"actor_id": "alice"}, "permission_denied", "不得审核自己"),
        (
            {"events": (("dora", "domain_pass"),)},
            "duplicate",
            "该角色已经通过",
        ),
        (
            {"checklist": {"method_complete": True}},
            "invalid_input",
            "未全部通过",
        ),
        (
            {"action": "domain_needs_changes", "checklist": None, "reason": None},
            "invalid_input",
            "必须填写原因",
        ),
        ({"action": "withdraw"}, "invalid_input", "未知审核动作"),
        ({"role": ROLE_COMPLIANCE}, "permission_denied", "需要角色"),
    ],
)
def test_review_decision_blocks_existing_review_errors(overrides, kind, message):
    """状态、自审、重复签署、清单、原因、动作与角色校验都留在协议内。"""
    base = {
        "status": "pending",
        "contributor_id": "alice",
        "actor_id": "dora",
        "role": ROLE_DOMAIN,
        "action": "domain_pass",
        "checklist": _checks(ROLE_DOMAIN),
        "reason": None,
        "events": (),
    }
    with pytest.raises(EvidenceError) as blocked:
        review_decision(**{**base, **overrides})
    assert blocked.value.kind == kind
    assert message in str(blocked.value)


def test_review_decision_allows_same_actor_signing_both_roles():
    """同一账号持双角色时可依次签领域与合规，协议只拦同角色重复签署。"""
    state = review_decision(
        status="pending",
        contributor_id="alice",
        actor_id="dora",
        role=ROLE_COMPLIANCE,
        action="compliance_pass",
        checklist=_checks(ROLE_COMPLIANCE),
        reason=None,
        events=(("dora", "domain_pass"),),
    )
    assert state == "accepted"
