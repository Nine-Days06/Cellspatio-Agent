"""已校验分析结果与当前人工事实；消费者不再拼装有效性判断。"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.analysis.review_standing import review_is_current
from src.analysis.sc_cluster import checked_completed_cluster
from src.analysis.sc_marker_reviews import CLUSTER_RECORD_VERSION, review_binding
from src.analysis.sc_markers import MARKER_REVIEW_VERSION, checked_completed_marker
from src.analysis.sc_pca import DIMENSION_RECORD_VERSION
from src.ui.run_snapshot import RunSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewFacts:
    """保留历史记录及失效原因；只有 current 可作为当前复核证据。"""

    result: dict[str, Any] | None
    latest: dict[str, Any] | None
    current: dict[str, Any] | None
    invalidated_reason: str | None
    review_invalidated_reason: str | None
    blocked: bool


def cluster_review_facts(run: RunSnapshot, attempt: dict[str, Any], root: Path) -> ReviewFacts:
    """重验聚类授权父链，接受与拒绝都绑定同一份真实结果。"""
    result, binding, invalid = None, None, None
    if attempt["status"] == "completed":
        try:
            checked_completed_cluster(run, attempt, root, DIMENSION_RECORD_VERSION)
            result = attempt["result"]
            binding = review_binding(run, attempt)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
            StopIteration,
            IndexError,
        ) as exc:
            logger.warning("cluster evidence validation failed: %s", type(exc).__name__)
            invalid = "聚类来源、协议或本地产物不符；仅保留历史，不能记录接受"
    decisions = [
        {**review, "outcome": "accepted"} for review in run.cluster_reviews(attempt["attempt_id"])
    ] + [
        {**review, "outcome": "rejected"}
        for review in run.cluster_rejections(attempt["attempt_id"])
    ]
    latest = max(decisions, key=lambda review: review["source_revision"], default=None)
    blocked = bool(run.running_stages())
    current = (
        latest
        if latest
        and (latest["outcome"] == "rejected" or latest.get("accepted") is True)
        and latest.get("record_version") == CLUSTER_RECORD_VERSION
        and latest.get("binding_sha256") == binding
        and review_is_current(
            latest, run["revision"], blocked=blocked, attempt_valid=result is not None
        )
        else None
    )
    return ReviewFacts(
        result=result,
        latest=latest,
        current=current,
        invalidated_reason=invalid,
        review_invalidated_reason="来源或 revision 已变化；该事实仅作历史"
        if latest and current is None
        else None,
        blocked=blocked,
    )


def marker_review_facts(run: RunSnapshot, attempt: dict[str, Any], root: Path) -> ReviewFacts:
    """重验自己的授权父链及真实产物，再绑定最新版本的人工复核。"""
    result, invalid = None, None
    if attempt["status"] == "completed":
        try:
            checked_completed_marker(run, attempt, root)
            # validator 已核对 metadata 与保存结果相等，保留构卡所需的产物身份。
            result = attempt["result"]
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
            StopIteration,
            IndexError,
        ) as exc:
            logger.warning("marker evidence validation failed: %s", type(exc).__name__)
            invalid = "来源、最新聚类接受、协议或产物变化；本次 marker 仅为历史"
    history = run.marker_reviews(attempt["attempt_id"])
    latest = history[-1] if history else None
    blocked = bool(run.running_stages())
    current = (
        latest
        if latest
        and latest.get("record_version") == MARKER_REVIEW_VERSION
        and latest.get("binding_sha256") == attempt["binding_sha256"]
        and review_is_current(
            latest, run["revision"], blocked=blocked, attempt_valid=result is not None
        )
        else None
    )
    return ReviewFacts(
        result=result,
        latest=latest,
        current=current,
        invalidated_reason=invalid,
        review_invalidated_reason="记录已失效；请刷新后重新复核"
        if latest and current is None
        else None,
        blocked=blocked,
    )
