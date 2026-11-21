"""公共证据治理的领域失败：稳定类别与 HTTP 解耦。"""

from __future__ import annotations

from typing import Literal

ErrorKind = Literal[
    "invalid_input",
    "submission_not_found",
    "permission_denied",
    "duplicate",
    "invalid_state",
    "quota_exceeded",
]


class EvidenceError(ValueError):
    """带稳定类别的领域失败；HTTP 响应中以 `code` 输出该 `kind`。"""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        """记录领域类别与面向用户的说明。"""
        super().__init__(message)
        self.kind = kind
