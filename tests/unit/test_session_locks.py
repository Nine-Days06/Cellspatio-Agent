"""会话锁注册表与占锁协议。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.session_locks import BUSY_DETAIL, lock_for, session_guard


def _sid() -> str:
    return uuid4().hex


def test_same_session_is_mutually_exclusive_with_409():
    sid = _sid()
    with session_guard(sid):
        with pytest.raises(HTTPException) as excinfo:
            with session_guard(sid):
                pass
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == BUSY_DETAIL


def test_released_after_success():
    sid = _sid()
    with session_guard(sid):
        pass
    assert lock_for(sid).acquire(blocking=False) is True
    lock_for(sid).release()


def test_released_after_exception():
    sid = _sid()
    with pytest.raises(RuntimeError):
        with session_guard(sid):
            raise RuntimeError("boom")
    assert lock_for(sid).acquire(blocking=False) is True
    lock_for(sid).release()


def test_different_sessions_do_not_block_each_other():
    with session_guard(_sid()):
        with session_guard(_sid()):
            pass


def test_lock_reused_for_same_session():
    sid = _sid()
    assert lock_for(sid) is lock_for(sid)
