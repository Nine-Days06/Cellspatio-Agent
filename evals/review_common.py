"""知识问答复核的共享判定：评测器与复核 CLI 共用，避免双份阈值/选择规则。"""

from __future__ import annotations

from typing import Any

QUALITY_CASE_COUNT = 12
QUALITY_ACCEPTED_THRESHOLD = 10


def latest_reviews(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """同一答案按追加顺序取最新复核；SessionStore 已按 created_at,rowid 排序。"""
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        latest[review["answer_id"]] = review
    return latest


def current_review(
    record: dict[str, Any], latest: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """取与当前答案绑定一致的复核；旧绑定的复核视为不存在。"""
    review = latest.get((record.get("answer") or {}).get("answer_id"))
    if review and review.get("binding_sha256") == record.get("binding_sha256"):
        return review
    return None


def review_counts(
    records: list[dict[str, Any]], latest: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """统计当前绑定有效的 accepted 与否决项数量。"""
    return {
        "accepted": sum(
            bool(
                (review := current_review(record, latest)) and review.get("decision") == "accepted"
            )
            for record in records
        ),
        "vetoes": sum(
            1
            for record in records
            if (review := current_review(record, latest)) and review.get("veto")
        ),
    }


def quality_claim_allowed(
    cases: list[dict[str, Any]],
    records: list[dict[str, Any]],
    reviews: list[dict[str, Any]] | None = None,
    *,
    threshold_confirmed: bool = False,
) -> bool:
    """判断是否满足完整样本、人工阈值且无否决项的质量声明条件。"""
    if not threshold_confirmed or len(cases) != QUALITY_CASE_COUNT:
        return False
    if len(records) != len(cases):
        return False
    case_ids = {case.get("id") for case in cases}
    if {record.get("case_id") for record in records} != case_ids:
        return False
    approved = all(
        case.get("status") == "approved"
        and case.get("source") not in {"model-drafted", "model_drafted"}
        for case in cases
    )
    answered = all(record["answer"].get("status") == "success" for record in records)
    latest = latest_reviews(reviews or [])
    current = [current_review(record, latest) for record in records]
    if any(review is None for review in current):
        return False
    accepted = sum(
        bool((review := current_review(record, latest)) and review.get("decision") == "accepted")
        for record in records
    )
    vetoed = any(review.get("veto") for review in current if review)
    return approved and answered and accepted >= QUALITY_ACCEPTED_THRESHOLD and not vetoed
