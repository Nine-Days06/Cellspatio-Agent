"""公共证据服务：复核队列、审计事件、引用条目与写命令的领域编排。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from src.evidence.contribution import ContributionPreview, build_envelope, preview_card
from src.evidence.errors import EvidenceError
from src.evidence.governance import STATE_NEEDS_CHANGES, GovernanceStore
from src.evidence.review_protocol import (
    action_role,
    action_spec,
    checklist_for_role,
    viewer_access,
    withdraw_role,
)
from src.evidence.schema import EvidenceCard
from src.public_api.context import reviewer_context


class ReviewCommand(Protocol):
    """复核命令字段；ReviewRequest 可直接满足该结构。"""

    action: str
    checklist: dict[str, Any] | None
    reason: str | None


class WithdrawCommand(Protocol):
    """撤销命令字段；WithdrawRequest 可直接满足该结构。"""

    reason: str


class PublicEvidenceService:
    """把治理库输出组装成 HTTP 响应形状，并承载写命令的领域编排。"""

    def __init__(self, store: GovernanceStore) -> None:
        """绑定当前进程的治理库。"""
        self._store = store

    def submit(
        self,
        preview: Mapping[str, Any],
        consent: bool,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """用登录身份覆盖贡献者与时间后接收提交。"""
        envelope = build_envelope(preview, consent=consent)
        envelope.contributor_id = user["id"]
        envelope.submitted_at = None
        return self._store.submit(envelope)

    def preview(
        self,
        card: EvidenceCard | Mapping[str, Any],
        user: dict[str, Any],
        revises_submission_id: str | None = None,
    ) -> ContributionPreview:
        """生成上传前预览；修订归属与状态失败直接抛既有错误类别。"""
        if revises_submission_id:
            original = self._store.summary(revises_submission_id)
            if original["contributor_id"] != user["id"]:
                raise EvidenceError("permission_denied", "只能修订自己的提交")
            if original["status"] != STATE_NEEDS_CHANGES:
                raise EvidenceError("invalid_state", "只能修订 needs_changes 的提交")
        parsed = EvidenceCard.model_validate(card)
        return preview_card(
            parsed,
            contributor_id=user["id"],
            revises_submission_id=revises_submission_id,
        )

    def review(
        self,
        evidence_id: str,
        body: ReviewCommand,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """校验动作与角色后记录复核；body 提供 action/checklist/reason。"""
        role = action_role(body.action)
        if role is None:
            raise EvidenceError("invalid_input", "未知审核动作")
        if role not in user["roles"]:
            raise EvidenceError("permission_denied", f"动作 {body.action} 需要角色 {role}")
        return self._store.review(
            evidence_id,
            actor_id=user["id"],
            role=role,
            action=body.action,
            checklist=body.checklist,
            reason=body.reason,
        )

    def withdraw(
        self,
        evidence_id: str,
        body: WithdrawCommand,
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """按摘要解析撤回角色后撤销；body 提供 reason。"""
        summary = self._store.summary(evidence_id)
        role = withdraw_role(
            actor_id=user["id"],
            contributor_id=summary["contributor_id"],
            roles=user["roles"],
        )
        return self._store.withdraw(
            evidence_id,
            actor_id=user["id"],
            role=role,
            reason=body.reason,
        )

    def queue(self, viewer: dict[str, Any]) -> dict[str, Any]:
        """复核队列：待审、本角色未签、本人未审，排除自己的提交。"""
        ctx = reviewer_context(viewer)
        return {
            "role": ctx.role,
            "queue": self._store.review_queue(viewer_actor=ctx.actor_id, viewer_role=ctx.role),
        }

    def events(self, evidence_id: str, viewer: dict[str, Any]) -> dict[str, Any]:
        """复核者查看审计事件，并一次返回当前查看者的复核能力。"""
        ctx = reviewer_context(viewer)
        view = self._store.view(evidence_id)
        summary, records = view["summary"], view["events"]
        access = viewer_access(
            status=summary["status"],
            contributor_id=summary["contributor_id"],
            viewer_actor=ctx.actor_id,
            viewer_role=ctx.role,
            events=[(event["actor_id"], event["action"]) for event in records],
        )
        return {
            "events": records,
            "review": {
                "role": ctx.role,
                "actions": [action_spec(action) for action in access.actions],
                "blocked_reason": access.blocked_reason,
                "blocked_code": access.blocked_code,
                "checklists": {ctx.role: list(checklist_for_role(ctx.role))},
            },
        }

    def citation(self, evidence_id: str) -> dict[str, Any]:
        """引用索引：只对 accepted 证据给出可引用条目；撤销后 404。"""
        data = self._store.get(evidence_id)
        card = data.get("card")
        if card is None:
            raise EvidenceError("submission_not_found", "证据已撤销或不可引用")
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
