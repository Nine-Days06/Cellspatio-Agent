"""公共证据复核协议：不依赖数据库与界面的纯规则。

给定提交状态、已有事件、查看者身份与角色，回答状态迁移、可用动作、
阻断原因和审核队列资格。持久化与 HTTP 层只负责提供输入并执行结论。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.evidence.errors import EvidenceError

ROLE_CONTRIBUTOR = "contributor"
ROLE_DOMAIN = "domain_reviewer"
ROLE_COMPLIANCE = "compliance_reviewer"

STATE_PENDING = "pending"
STATE_NEEDS_CHANGES = "needs_changes"
STATE_ACCEPTED = "accepted"
STATE_REJECTED = "rejected"
STATE_WITHDRAWN = "withdrawn"

ACTION_SUBMIT = "submit"
ACTION_DOMAIN_PASS = "domain_pass"
ACTION_COMPLIANCE_PASS = "compliance_pass"
ACTION_DOMAIN_NEEDS_CHANGES = "domain_needs_changes"
ACTION_COMPLIANCE_NEEDS_CHANGES = "compliance_needs_changes"
ACTION_DOMAIN_REJECT = "domain_reject"
ACTION_COMPLIANCE_REJECT = "compliance_reject"
ACTION_WITHDRAW = "withdraw"

DOMAIN_CHECKLIST = ("method_complete", "claim_matches_observation", "limitations_present")
COMPLIANCE_CHECKLIST = (
    "privacy_scan_passed",
    "license_and_attribution_present",
    "no_raw_data",
    "small_sample_ok",
)

ACTION_ROLE = {
    ACTION_DOMAIN_PASS: ROLE_DOMAIN,
    ACTION_DOMAIN_NEEDS_CHANGES: ROLE_DOMAIN,
    ACTION_DOMAIN_REJECT: ROLE_DOMAIN,
    ACTION_COMPLIANCE_PASS: ROLE_COMPLIANCE,
    ACTION_COMPLIANCE_NEEDS_CHANGES: ROLE_COMPLIANCE,
    ACTION_COMPLIANCE_REJECT: ROLE_COMPLIANCE,
}
REVIEW_ACTIONS = tuple(ACTION_ROLE)
PASS_ACTIONS = (ACTION_DOMAIN_PASS, ACTION_COMPLIANCE_PASS)
REJECT_ACTIONS = (ACTION_DOMAIN_REJECT, ACTION_COMPLIANCE_REJECT)
NEEDS_CHANGES_ACTIONS = (ACTION_DOMAIN_NEEDS_CHANGES, ACTION_COMPLIANCE_NEEDS_CHANGES)

Event = tuple[str, str]


@dataclass(frozen=True)
class ReviewAccess:
    """查看者在一份提交上的可用复核动作与阻断原因。"""

    actions: tuple[str, ...]
    blocked_reason: str | None


def action_role(action: str) -> str | None:
    """复核动作对应的角色；非复核动作返回 None。"""
    return ACTION_ROLE.get(action)


def checklist_for_role(role: str) -> tuple[str, ...]:
    """角色必须全部勾选的清单键；无复核角色返回空元组。"""
    if role == ROLE_DOMAIN:
        return DOMAIN_CHECKLIST
    if role == ROLE_COMPLIANCE:
        return COMPLIANCE_CHECKLIST
    return ()


def reviewer_role(roles: Iterable[str]) -> str | None:
    """账号的主复核角色：领域优先，其次合规；都没有则 None。"""
    role_set = set(roles)
    if ROLE_DOMAIN in role_set:
        return ROLE_DOMAIN
    if ROLE_COMPLIANCE in role_set:
        return ROLE_COMPLIANCE
    return None


def next_state(action: str, prior_actions: Iterable[str] = ()) -> str:
    """应用 action 后的提交状态；`prior_actions` 不含本次动作。"""
    actions = set(prior_actions)
    actions.add(action)
    if action == ACTION_WITHDRAW:
        return STATE_WITHDRAWN
    if action in REJECT_ACTIONS:
        return STATE_REJECTED
    if action in NEEDS_CHANGES_ACTIONS:
        return STATE_NEEDS_CHANGES
    if set(PASS_ACTIONS) <= actions:
        return STATE_ACCEPTED
    return STATE_PENDING


def queue_eligible(
    *,
    status: str,
    contributor_id: str,
    viewer_actor: str,
    viewer_role: str,
    events: Iterable[Event],
) -> bool:
    """是否应出现在该查看者的审核队列：pending、非本人、本人未审、本角色未签。"""
    if viewer_role not in (ROLE_DOMAIN, ROLE_COMPLIANCE):
        return False
    if status != STATE_PENDING or contributor_id == viewer_actor:
        return False
    pass_action = ACTION_DOMAIN_PASS if viewer_role == ROLE_DOMAIN else ACTION_COMPLIANCE_PASS
    for actor_id, action in events:
        if actor_id == viewer_actor or action == pass_action:
            return False
    return True


def viewer_access(
    *,
    status: str,
    contributor_id: str,
    viewer_actor: str,
    viewer_role: str,
    events: Iterable[Event],
) -> ReviewAccess:
    """查看者可执行的动作；不可执行时给出与现有页面一致的阻断原因。"""
    if viewer_role not in (ROLE_DOMAIN, ROLE_COMPLIANCE):
        return ReviewAccess((), "当前账号没有复核角色。")
    if status != STATE_PENDING:
        return ReviewAccess((), "该提交已结束审核。")
    if contributor_id == viewer_actor:
        return ReviewAccess((), "贡献者不得审核自己的提交")

    prefix = "domain" if viewer_role == ROLE_DOMAIN else "compliance"
    pass_action = f"{prefix}_pass"
    records = list(events)
    if any(action == pass_action for _, action in records):
        return ReviewAccess((), "本角色已签署，等待另一角色复核。")
    if any(actor_id == viewer_actor for actor_id, _ in records):
        return ReviewAccess((), "你已复核过该提交。")
    return ReviewAccess((f"{prefix}_pass", f"{prefix}_needs_changes", f"{prefix}_reject"), None)


def withdraw_role(*, actor_id: str, contributor_id: str, roles: Iterable[str]) -> str:
    """撤销角色：本人按贡献者处理，否则只有合规复核者可强制撤销。"""
    if actor_id == contributor_id:
        return ROLE_CONTRIBUTOR
    if ROLE_COMPLIANCE in set(roles):
        return ROLE_COMPLIANCE
    raise EvidenceError("permission_denied", "只能撤回自己的提交")
