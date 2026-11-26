"""路由共用的领域异常集合。"""

from src.ui.store_errors import StoreError

EVIDENCE_ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    OverflowError,
    StopIteration,
    IndexError,
)

_STORE_STATUS = {"not_found": 404, "conflict": 409, "invalid": 400}


def store_status(exc: StoreError) -> int:
    """存储错误到 HTTP 状态的唯一映射。"""
    return _STORE_STATUS[exc.kind]
