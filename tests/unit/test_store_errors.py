"""会话/项目生命周期错误的稳定类别与 HTTP 映射。"""

import pytest

from src.api.errors import store_status
from src.ui.session_store import SessionStore
from src.ui.store_errors import StoreError


def test_create_session_reports_missing_and_archived_project() -> None:
    """项目缺失=not_found，已归档=conflict，状态不再靠中文推断。"""
    store = SessionStore()
    pid = store.create_project("归档项目")
    store.update_project(pid, archived=True)

    with pytest.raises(StoreError) as missing:
        store.create_session(project_id="nope")
    assert (missing.value.kind, store_status(missing.value)) == ("not_found", 404)

    with pytest.raises(StoreError) as archived:
        store.create_session(project_id=pid)
    assert (archived.value.kind, store_status(archived.value)) == ("conflict", 409)


def test_archive_session_while_running_is_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """运行中的阶段是状态冲突，与“会话不存在”区分。"""
    store = SessionStore()
    sid = store.create_session()
    monkeypatch.setattr(store, "running_stage", lambda _session_id: "过滤")

    with pytest.raises(StoreError) as running:
        store.archive_session(sid)
    assert (running.value.kind, store_status(running.value)) == ("conflict", 409)


def test_move_session_reports_missing_project() -> None:
    """移动目标缺失是 not_found。"""
    store = SessionStore()
    sid = store.create_session()

    with pytest.raises(StoreError) as missing:
        store.move_session(sid, "nope")
    assert (missing.value.kind, store_status(missing.value)) == ("not_found", 404)
