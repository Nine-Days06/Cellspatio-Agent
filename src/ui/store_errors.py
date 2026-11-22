"""会话/项目生命周期的稳定失败类别；HTTP 映射留在 API 层。"""

from typing import Literal

StoreErrorKind = Literal["not_found", "conflict"]


class StoreError(ValueError):
    """存储层拒绝执行，``kind`` 说明缺资源还是状态冲突。"""

    def __init__(self, kind: StoreErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
