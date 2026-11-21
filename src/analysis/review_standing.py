"""人工复核事实是否仍是当前有效记录的单一判定。"""

from typing import Any


def review_is_current(
    record: dict[str, Any],
    run_revision: int,
    *,
    blocked: bool = False,
    attempt_valid: bool = True,
) -> bool:
    """严格 revision 加当前来源/产物校验；任一失败都只能作历史。"""
    return attempt_valid and not blocked and record["source_revision"] + 1 == run_revision
