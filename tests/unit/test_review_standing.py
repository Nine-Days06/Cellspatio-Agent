"""人工复核当前有效性的单一判定。"""

import pytest

from src.analysis.review_standing import review_is_current


@pytest.mark.parametrize(
    ("blocked", "attempt_valid", "current"),
    [(False, True, True), (True, True, False), (False, False, False)],
)
def test_review_is_current_requires_revision_and_validation(
    blocked: bool, attempt_valid: bool, current: bool
) -> None:
    """严格 revision、来源未阻断且产物有效才是当前记录。"""
    assert (
        review_is_current(
            {"source_revision": 3},
            4,
            blocked=blocked,
            attempt_valid=attempt_valid,
        )
        is current
    )


def test_review_is_current_rejects_stale_revision() -> None:
    """非 latest+1 的 revision 只能作历史。"""
    assert not review_is_current({"source_revision": 2}, 4)
