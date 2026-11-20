"""共享阶段生命周期 seam 的领取、幂等、冲突与终态骨架。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from src.ui.session_store import SessionStore
from src.ui.stage_lifecycle import StageLifecycle, StageSpec


def fake_message(attempt: sqlite3.Row, result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """用测试阶段自己的文案证明消息由描述驱动。"""
    return f"fake {result['status']}", {
        "fake": {"attempt_id": attempt["attempt_id"], "status": result["status"]}
    }


FAKE_STAGE = StageSpec(
    table="fake_attempts",
    match_columns=("worker",),
    terminal=("completed", "failed"),
    missing_run="QC 关联不存在",
    revision_error="阶段 revision 已变化，请刷新",
    invalid_terminal="fake 终态非法",
    not_running="fake 任务不在运行状态",
    message=fake_message,
)


def encode(results: dict[str, Any]) -> str:
    """测试阶段使用普通 JSON 编码。"""
    return json.dumps(results, ensure_ascii=False)


@pytest.fixture
def fake_store(tmp_path: Path) -> tuple[SessionStore, str]:
    """临时库中建立会话、QC run 与测试阶段表。"""
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session()
    store.register_qc_run(sid, "run", str(tmp_path / "run"), {"kind": "test"})
    with store._conn() as conn:
        conn.execute(
            "CREATE TABLE fake_attempts ("
            "attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, worker TEXT NOT NULL, "
            "directory TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, "
            "finished_at TEXT, result TEXT, message_id INTEGER, UNIQUE(run_id, worker))"
        )
    return store, sid


def claim(
    store: SessionStore,
    sid: str,
    *,
    revision: int = 0,
    attempt_id: str = "a1",
    worker: str = "w1",
    validate: Callable[[sqlite3.Connection, sqlite3.Row], None] | None = None,
) -> dict[str, Any]:
    """用固定描述领取一次测试阶段。"""
    return StageLifecycle(store._conn, encode).claim(
        FAKE_STAGE,
        session_id=sid,
        run_id="run",
        revision=revision,
        attempt_id=attempt_id,
        values={"worker": worker, "directory": f"/tmp/{attempt_id}"},
        validate=validate or (lambda conn, run: None),
        clock=lambda: "2026-11-20T00:00:00+00:00",
    )


def test_claim_is_idempotent_by_match_and_advances_revision_once(
    fake_store: tuple[SessionStore, str],
) -> None:
    """同一匹配只领取一次，且领取不改会话排序时间。"""
    store, sid = fake_store
    before = store.get_session(sid)["updated_at"]
    first = claim(store, sid)
    assert first["claimed"] is True and first["attempt_id"] == "a1"
    again = claim(store, sid, attempt_id="a2")
    assert again["claimed"] is False and again["attempt_id"] == "a1"
    assert again["directory"] == "/tmp/a1"
    assert store.get_qc_run(sid, "run")["revision"] == 1
    assert store.get_session(sid)["updated_at"] == before


def test_claim_rejects_stale_revision_and_failed_parent(
    fake_store: tuple[SessionStore, str],
) -> None:
    """父状态或 revision 不满足时不插入 attempt。"""
    store, sid = fake_store

    def blocked(conn: sqlite3.Connection, run: sqlite3.Row) -> None:
        """模拟父阶段尚未满足。"""
        raise ValueError("父状态不满足")

    with pytest.raises(ValueError, match="父状态不满足"):
        claim(store, sid, validate=blocked)
    assert store.get_qc_run(sid, "run")["revision"] == 0
    with pytest.raises(ValueError, match="revision"):
        claim(store, sid, revision=9)
    assert store.get_qc_run(sid, "run")["revision"] == 0
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fake_attempts").fetchone()[0] == 0
    with pytest.raises(ValueError, match="QC 关联不存在"):
        StageLifecycle(store._conn, encode).claim(
            FAKE_STAGE,
            session_id=sid,
            run_id="missing",
            revision=0,
            attempt_id="a3",
            values={"worker": "w3", "directory": "/tmp/a3"},
            validate=lambda conn, run: None,
            clock=lambda: "2026-11-20T00:00:00+00:00",
        )


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_finish_publishes_described_message_and_terminal_state(
    fake_store: tuple[SessionStore, str], status: str
) -> None:
    """终态与消息均由阶段描述驱动。"""
    store, sid = fake_store
    claim(store, sid)
    StageLifecycle(store._conn, encode).finish(
        FAKE_STAGE,
        attempt_id="a1",
        result={"status": status},
        clock=lambda: "2026-11-20T00:01:00+00:00",
    )
    run = store.get_qc_run(sid, "run")
    assert run["revision"] == 2
    with store._conn() as conn:
        attempt = conn.execute("SELECT * FROM fake_attempts WHERE attempt_id = 'a1'").fetchone()
    assert attempt["status"] == status and attempt["message_id"]
    message = store.get_messages(sid)[-1]
    assert message["content"] == f"fake {status}"
    assert message["results"]["fake"] == {"attempt_id": "a1", "status": status}


def test_finish_rejects_invalid_or_repeated_terminal(
    fake_store: tuple[SessionStore, str],
) -> None:
    """非法终态与重复完成都保持失败关闭。"""
    store, sid = fake_store
    claim(store, sid)
    lifecycle = StageLifecycle(store._conn, encode)
    with pytest.raises(ValueError, match="终态非法"):
        lifecycle.finish(
            FAKE_STAGE,
            attempt_id="a1",
            result={"status": "interrupted"},
            clock=lambda: "2026-11-20T00:01:00+00:00",
        )
    lifecycle.finish(
        FAKE_STAGE,
        attempt_id="a1",
        result={"status": "completed"},
        clock=lambda: "2026-11-20T00:01:00+00:00",
    )
    with pytest.raises(ValueError, match="不在运行状态"):
        lifecycle.finish(
            FAKE_STAGE,
            attempt_id="a1",
            result={"status": "failed"},
            clock=lambda: "2026-11-20T00:02:00+00:00",
        )
    with pytest.raises(ValueError, match="不在运行状态"):
        lifecycle.finish(
            FAKE_STAGE,
            attempt_id="missing",
            result={"status": "completed"},
            clock=lambda: "2026-11-20T00:02:00+00:00",
        )
