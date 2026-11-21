"""复核事实追加接缝：事务、幂等、重复抑制、消息、revision 与回滚。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from src.ui.session_store import SessionStore
from src.ui.stage_lifecycle import ReviewFactPlan, ReviewFactResult, append_review_fact

NOW = "2026-11-21T00:00:00+00:00"


def encode(results: dict[str, Any]) -> str:
    """测试消息使用普通 JSON 编码。"""
    return json.dumps(results, ensure_ascii=False)


@pytest.fixture
def world(tmp_path: Path) -> tuple[SessionStore, str]:
    """临时库：真实会话表 + 假复核事实表。"""
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session()
    with store._conn() as conn:
        conn.execute(
            "CREATE TABLE fake_reviews ("
            "review_id TEXT PRIMARY KEY, fake_attempt_id TEXT NOT NULL, "
            "binding_sha256 TEXT NOT NULL, record_version TEXT NOT NULL, "
            "source_revision INTEGER NOT NULL, payload TEXT NOT NULL, reason TEXT NOT NULL, "
            "created_at TEXT NOT NULL, message_id INTEGER, "
            "UNIQUE(fake_attempt_id, binding_sha256, record_version, source_revision))"
        )
    return store, sid


def review_plan(
    *,
    attempt_id: str = "a1",
    binding: str = "b1",
    version: str = "v1",
    revision: int = 3,
    payload: str = "p1",
    reason: str = "r1",
    message: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]] | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    latest: bool = True,
) -> ReviewFactPlan:
    """构造假表的复核计划；默认覆盖 previous 与 latest 两种匹配。"""

    def same_previous(row: sqlite3.Row) -> bool:
        """同 revision 内容一致才幂等。"""
        return row["payload"] == payload and row["reason"] == reason

    def same_latest(row: sqlite3.Row) -> bool:
        """latest+1 内容一致才抑制重复。"""
        return (
            row["binding_sha256"] == binding
            and row["record_version"] == version
            and row["payload"] == payload
            and row["reason"] == reason
        )

    return ReviewFactPlan(
        table="fake_reviews",
        previous_match={
            "fake_attempt_id": attempt_id,
            "binding_sha256": binding,
            "record_version": version,
            "source_revision": revision,
        },
        insert_values={
            "fake_attempt_id": attempt_id,
            "binding_sha256": binding,
            "record_version": version,
            "source_revision": revision,
            "payload": payload,
            "reason": reason,
        },
        same_previous=same_previous,
        conflict_message="同一 revision 的复核内容冲突",
        latest_match={"fake_attempt_id": attempt_id} if latest else None,
        same_latest=same_latest,
        message=message,
        run_id=run_id,
        session_id=session_id,
    )


def append(store: SessionStore, plan: ReviewFactPlan, **kwargs: Any) -> ReviewFactResult:
    """以固定时钟调用接缝。"""
    return append_review_fact(store._conn, encode, plan, clock=lambda: NOW, **kwargs)


def test_first_insert_then_same_revision_is_idempotent(world: tuple[SessionStore, str]) -> None:
    """首次插入产生事实行；同 revision 重发只返回既有行。"""
    store, _ = world
    first = append(store, review_plan())
    assert first.inserted is True
    assert first.fact["payload"] == "p1"
    assert first.fact["review_id"]
    assert first.fact["created_at"] == NOW

    again = append(store, review_plan())
    assert again.inserted is False
    assert again.fact["review_id"] == first.fact["review_id"]
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fake_reviews").fetchone()[0] == 1


def test_previous_conflict_is_rejected_without_writing(
    world: tuple[SessionStore, str],
) -> None:
    """同 revision 内容不一致时按计划文案报错且不新增。"""
    store, _ = world
    append(store, review_plan())
    with pytest.raises(ValueError, match="内容冲突"):
        append(store, review_plan(payload="different"))
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fake_reviews").fetchone()[0] == 1


def test_latest_next_revision_duplicate_is_suppressed(
    world: tuple[SessionStore, str],
) -> None:
    """latest+1 且内容相同不新增事实，只返回最近一行。"""
    store, _ = world
    first = append(store, review_plan(revision=3))
    duplicate = append(store, review_plan(revision=4))
    assert duplicate.inserted is False
    assert duplicate.fact["review_id"] == first.fact["review_id"]
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fake_reviews").fetchone()[0] == 1


def test_message_callback_persists_one_assistant_message(
    world: tuple[SessionStore, str],
) -> None:
    """计划提供消息时，文案与结果由回调决定并绑定到事实行。"""
    store, sid = world

    def message(fact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """假阶段自己的文案与结构化结果。"""
        return "fake 复核已记录", {"fake_review": {"review_id": fact["review_id"]}}

    result = append(store, review_plan(message=message, session_id=sid))
    assert result.inserted is True
    assert result.fact["message_id"]
    published = store.get_messages(sid)[-1]
    assert published["content"] == "fake 复核已记录"
    assert published["results"]["fake_review"]["review_id"] == result.fact["review_id"]


def test_run_plan_advances_revision_and_session_time(
    world: tuple[SessionStore, str],
) -> None:
    """有 run 时才推进 revision，并刷新会话排序时间。"""
    store, sid = world
    store.register_qc_run(sid, "run", "fake/run", {"kind": "fake"})
    before = store.get_session(sid)["updated_at"]
    append(store, review_plan(revision=0, run_id="run", session_id=sid))
    assert store.get_qc_run(sid, "run")["revision"] == 1
    assert store.get_session(sid)["updated_at"] == NOW
    assert store.get_session(sid)["updated_at"] != before


def test_no_run_plan_leaves_existing_run_revision_untouched(
    world: tuple[SessionStore, str],
) -> None:
    """无 run、无 latest 的计划（如 QA 复核）不推进任何 revision。"""
    store, sid = world
    store.register_qc_run(sid, "run", "fake/run", {"kind": "fake"})
    result = append(store, review_plan(revision=0, latest=False))
    assert result.inserted is True
    assert store.get_qc_run(sid, "run")["revision"] == 0


def test_exception_rolls_back_message_and_fact(
    world: tuple[SessionStore, str],
) -> None:
    """插入失败时消息与事实一起回滚，run revision 不变。"""
    store, sid = world
    store.register_qc_run(sid, "run", "fake/run", {"kind": "fake"})
    messages_before = len(store.get_messages(sid))

    def message(fact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """回滚用例只需要一条可编码的消息。"""
        return "x", {}

    plan = review_plan(revision=0, run_id="run", session_id=sid, message=message)
    broken = replace(
        plan,
        insert_values={key: value for key, value in plan.insert_values.items() if key != "payload"},
    )
    with pytest.raises(sqlite3.IntegrityError):
        append(store, broken)
    assert len(store.get_messages(sid)) == messages_before
    assert store.get_qc_run(sid, "run")["revision"] == 0
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fake_reviews").fetchone()[0] == 0


def test_run_plan_requires_session_id(world: tuple[SessionStore, str]) -> None:
    """有 run 时必须同时提供 session，否则不进入事务。"""
    store, _ = world
    with pytest.raises(ValueError, match="session_id"):
        append(store, review_plan(run_id="run"))
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fake_reviews").fetchone()[0] == 0


def test_validate_order_is_configurable(world: tuple[SessionStore, str]) -> None:
    """默认 previous 幂等可跳过校验；validate_first 支持旧 revision 先报失效。"""
    store, _ = world
    append(store, review_plan())

    def stale(conn: sqlite3.Connection) -> None:
        """模拟 cluster rejection 的旧 revision 校验。"""
        raise ValueError("记录已失效，请刷新")

    assert append(store, review_plan(), validate=stale).inserted is False
    with pytest.raises(ValueError, match="已失效"):
        append(store, review_plan(), validate=stale, validate_first=True)


def test_before_insert_runs_after_latest_dedupe(
    world: tuple[SessionStore, str],
) -> None:
    """latest 已命中去重时不执行 before_insert；首次插入前才执行。"""
    store, _ = world
    append(store, review_plan(revision=3))

    def conflict(conn: sqlite3.Connection) -> None:
        """模拟同一 revision 已有接受事实。"""
        raise ValueError("同一 revision 已有接受事实")

    duplicate = append(store, review_plan(revision=4), before_insert=conflict)
    assert duplicate.inserted is False
    with pytest.raises(ValueError, match="已有接受事实"):
        append(store, review_plan(revision=5), before_insert=conflict)
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM fake_reviews").fetchone()[0] == 1
