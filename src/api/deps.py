"""FastAPI 共享依赖：会话存储在应用级 factory 之下按请求构造。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from src.ui.session_store import SessionStore


def get_store(request: Request) -> SessionStore:
    """由 app.state.store_factory 构造本请求的 SessionStore。

    FastAPI 在同一请求内缓存依赖结果，多处 Depends(get_store) 只会构造一次。
    SessionStore 每次操作独立建连，同步依赖与 async 端点共用同一实例是安全的。
    """
    factory: Callable[[], SessionStore] = request.app.state.store_factory
    return factory()


StoreDep = Annotated[SessionStore, Depends(get_store)]
