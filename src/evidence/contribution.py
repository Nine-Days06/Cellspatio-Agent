"""本地证据贡献预览：先看清完整将发送内容，再显式同意提交。

预览包含 card 与 submission 两部分；原始矩阵、barcode、坐标、样本级 ID 与绝对路径
不进入证据卡，也不会进入预览。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

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


def _now() -> str:
    """当前 UTC 时间（ISO 8601）。"""
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    """计算文本的 SHA256。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _payload_sha256(preview: dict[str, Any]) -> str:
    """对预览正文做稳定哈希，忽略哈希字段本身。"""
    body = {key: value for key, value in preview.items() if key != "preview_sha256"}
    return _sha256(json.dumps(body, ensure_ascii=False, sort_keys=True))


def preview_card(card: EvidenceCard, *, contributor_id: str) -> dict[str, Any]:
    """生成完整贡献预览；隐私不合格直接拒绝，准入问题只标记不阻断。"""
    validate_card(card)
    if not contributor_id.strip():
        raise ValueError("预览需要 contributor_id")
    card_json = card.model_dump_json()
    problems = admission_problems(card)
    preview: dict[str, Any] = {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "created_at": _now(),
        "card_id": card.card_id,
        "card_sha256": _sha256(card_json),
        "privacy": "passed",
        "admission": {
            "status": "passed" if not problems else "failed",
            "problems": problems,
        },
        "will_send": {
            "submission": {
                "schema_version": SUBMISSION_SCHEMA_VERSION,
                "contributor_id": contributor_id,
                "target_library": "public",
                "supersedes": [],
                "revises_submission_id": None,
                "donor_count": None,
                "independent_replications": 0,
                "consent": False,
                "consent_text_version": CONSENT_TEXT_VERSION,
            },
            "card": json.loads(card_json),
        },
        "consent_text": CONSENT_TEXT,
        "not_sent": list(NOT_SENT),
    }
    preview["preview_sha256"] = _payload_sha256(preview)
    return preview


def build_envelope(preview: dict[str, Any], *, consent: bool) -> SubmissionEnvelope:
    """把预览转成提交信封；必须显式同意且预览内容未被篡改。"""
    if not consent:
        raise ValueError("必须显式同意才能提交")
    if preview.get("schema_version") != PREVIEW_SCHEMA_VERSION:
        raise ValueError(f"预览 schema_version 必须是 {PREVIEW_SCHEMA_VERSION}")
    if preview.get("privacy") != "passed":
        raise ValueError("预览未通过隐私校验")
    if _payload_sha256(preview) != preview.get("preview_sha256"):
        raise ValueError("预览内容已被修改，请重新生成预览")
    will_send = preview.get("will_send") or {}
    card = EvidenceCard.model_validate(will_send.get("card") or {})
    if _sha256(card.model_dump_json()) != preview.get("card_sha256"):
        raise ValueError("预览中的证据卡已被修改")
    return SubmissionEnvelope.model_validate(
        {**will_send["submission"], "card": card, "consent": True}
    )
