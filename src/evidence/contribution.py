"""本地证据贡献预览：先看清完整将发送内容，再显式同意提交。

预览包含 card 与 submission 两部分；原始矩阵、barcode、坐标、样本级 ID 与绝对路径
不进入证据卡，也不会进入预览。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from src.evidence.errors import EvidenceError
from src.evidence.governance import (
    CONSENT_TEXT,
    CONSENT_TEXT_VERSION,
    SUBMISSION_SCHEMA_VERSION,
    SubmissionEnvelope,
    admission_problems,
)
from src.evidence.schema import EvidenceCard, validate_card

PREVIEW_SCHEMA_VERSION = "cellspatio-evidence-contribution-preview/1.0.0"
NOT_SENT = (
    "原始矩阵与逐细胞数据",
    "barcode",
    "空间坐标",
    "样本级 ID",
    "主机绝对路径",
)


class Admission(TypedDict):
    """准入检查结果。"""

    status: Literal["passed", "failed"]
    problems: list[str]


class WillSendSubmission(TypedDict):
    """将随证据卡一起发送的提交元数据。"""

    schema_version: str
    contributor_id: str
    target_library: Literal["public"]
    supersedes: list[str]
    revises_submission_id: str | None
    donor_count: int | None
    independent_replications: int
    consent: bool
    consent_text_version: str


class WillSend(TypedDict):
    """预览中明确列出的将发送内容。"""

    submission: WillSendSubmission
    card: dict[str, Any]


class ContributionPreview(TypedDict):
    """贡献预览的完整形状；JSON 序列化形状必须保持稳定。"""

    schema_version: str
    created_at: str
    card_id: str
    card_sha256: str
    privacy: Literal["passed"]
    admission: Admission
    will_send: WillSend
    consent_text: str
    not_sent: list[str]
    preview_sha256: str


def _now() -> str:
    """当前 UTC 时间（ISO 8601）。"""
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    """计算文本的 SHA256。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _payload_sha256(preview: Mapping[str, Any]) -> str:
    """对预览正文做稳定哈希，忽略哈希字段本身。"""
    body = {key: value for key, value in preview.items() if key != "preview_sha256"}
    return _sha256(json.dumps(body, ensure_ascii=False, sort_keys=True))


def preview_card(
    card: EvidenceCard,
    *,
    contributor_id: str,
    supersedes: list[str] | None = None,
    revises_submission_id: str | None = None,
) -> ContributionPreview:
    """生成完整贡献预览；隐私不合格直接拒绝，准入问题只标记不阻断。"""
    try:
        validate_card(card)
    except ValueError as exc:
        raise EvidenceError("invalid_input", f"证据卡校验失败：{exc}") from exc
    if not contributor_id.strip():
        raise EvidenceError("invalid_input", "预览需要 contributor_id")
    card_json = card.model_dump_json()
    problems = admission_problems(card)
    preview = ContributionPreview(
        schema_version=PREVIEW_SCHEMA_VERSION,
        created_at=_now(),
        card_id=card.card_id,
        card_sha256=_sha256(card_json),
        privacy="passed",
        admission={
            "status": "passed" if not problems else "failed",
            "problems": problems,
        },
        will_send={
            "submission": {
                "schema_version": SUBMISSION_SCHEMA_VERSION,
                "contributor_id": contributor_id,
                "target_library": "public",
                "supersedes": list(supersedes or []),
                "revises_submission_id": revises_submission_id,
                "donor_count": None,
                "independent_replications": 0,
                "consent": False,
                "consent_text_version": CONSENT_TEXT_VERSION,
            },
            "card": json.loads(card_json),
        },
        consent_text=CONSENT_TEXT,
        not_sent=list(NOT_SENT),
        preview_sha256="",
    )
    preview["preview_sha256"] = _payload_sha256(preview)
    return preview


def build_envelope(
    preview: ContributionPreview | Mapping[str, Any], *, consent: bool
) -> SubmissionEnvelope:
    """把预览转成提交信封；必须显式同意且预览内容未被篡改。"""
    if not consent:
        raise EvidenceError("invalid_input", "必须显式同意才能提交")
    if preview.get("schema_version") != PREVIEW_SCHEMA_VERSION:
        raise EvidenceError("invalid_input", f"预览 schema_version 必须是 {PREVIEW_SCHEMA_VERSION}")
    if preview.get("privacy") != "passed":
        raise EvidenceError("invalid_input", "预览未通过隐私校验")
    if _payload_sha256(preview) != preview.get("preview_sha256"):
        raise EvidenceError("invalid_input", "预览内容已被修改，请重新生成预览")
    will_send = preview.get("will_send")
    if not isinstance(will_send, dict):
        raise EvidenceError("invalid_input", "预览缺少 will_send")
    submission = will_send.get("submission")
    card_payload = will_send.get("card")
    if not isinstance(submission, dict) or not isinstance(card_payload, dict):
        raise EvidenceError("invalid_input", "预览缺少 submission 或 card")
    card = EvidenceCard.model_validate(card_payload)
    if _sha256(card.model_dump_json()) != preview.get("card_sha256"):
        raise EvidenceError("invalid_input", "预览中的证据卡已被修改")
    return SubmissionEnvelope.model_validate({**submission, "card": card, "consent": True})
