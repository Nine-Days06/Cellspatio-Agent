"""会话级互斥锁：锁注册表与占锁协议。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

BUSY_DETAIL = "该会话已有任务在进行"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(session_id: str) -> threading.Lock:
    """取该会话的生成锁；单用户单进程内存锁，每会话同时只允许一个生成任务。"""
    with _locks_guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _locks[session_id] = lock
        return lock


@contextmanager
def session_guard(session_id: str, *, detail: str = BUSY_DETAIL) -> Iterator[None]:
    """非阻塞占锁；已被占用时抛 409，成功获取后无论正常或异常退出都释放。"""
    lock = lock_for(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, detail)
    try:
        yield
    finally:
        lock.release()
