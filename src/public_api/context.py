"""复核者身份上下文：把账号字典收拢为协议需要的角色视图。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.evidence.errors import EvidenceError
from src.evidence.review_protocol import reviewer_role


@dataclass(frozen=True)
class ReviewerContext:
    """一次请求的复核者身份：账号、主复核角色与全部角色。"""

    actor_id: str
    role: str
    roles: tuple[str, ...]


def reviewer_context(user: dict[str, Any]) -> ReviewerContext:
    """解析主复核角色：领域优先，其次合规；无角色拒绝读取未发布全文。"""
    role = reviewer_role(user["roles"])
    if role is None:
        raise EvidenceError("permission_denied", "只有复核者可读取未发布全文")
    return ReviewerContext(actor_id=user["id"], role=role, roles=tuple(user["roles"]))
