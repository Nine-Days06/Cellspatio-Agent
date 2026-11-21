"""公共证据读服务：复核队列、审计事件与引用条目的响应组装。"""

from __future__ import annotations

from typing import Any

from src.evidence.errors import EvidenceError
from src.evidence.governance import GovernanceStore
from src.evidence.review_protocol import (
    action_spec,
    checklist_for_role,
    viewer_access,
)
from src.evidence.schema import EvidenceCard
from src.public_api.context import reviewer_context


class PublicEvidenceService:
    """把治理库输出组装成 HTTP 响应形状；复核角色解析走 ReviewerContext。"""

    def __init__(self, store: GovernanceStore) -> None:
        """绑定当前进程的治理库。"""
        self._store = store

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
        summary = self._store.summary(evidence_id)
        records = self._store.events(evidence_id)
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
