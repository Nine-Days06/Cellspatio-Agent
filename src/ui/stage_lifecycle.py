"""阶段 attempt 的通用领取与终态事务骨架；阶段差异由描述提供。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StageSpec:
    """一个受控阶段的持久化描述：表、终态、错误文案与消息形状。"""

    table: str
    match_columns: tuple[str, ...]
    terminal: tuple[str, ...]
    missing_run: str
    revision_error: str
    invalid_terminal: str
    not_running: str
    message: Callable[[sqlite3.Row, dict[str, Any]], tuple[str, dict[str, Any]]]


def publish_message(
    conn: sqlite3.Connection,
    session_id: str,
    content: str,
    results: dict[str, Any],
    now: str,
    encode_results: Callable[[dict[str, Any]], str],
) -> int:
    """在调用方事务内分配 seq 并插入一条 assistant 消息。"""
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO messages (session_id, seq, role, content, timestamp, results) "
        "VALUES (?, ?, 'assistant', ?, ?, ?)",
        (session_id, seq, content, now, encode_results(results)),
    ).lastrowid


def advance_run(
    conn: sqlite3.Connection,
    run_id: str,
    session_id: str,
    now: str,
    *,
    touch_session: bool = True,
) -> None:
    """同事务推进 revision，并按调用方要求刷新会话时间。"""
    conn.execute("UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run_id,))
    if touch_session:
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))


class StageLifecycle:
    """把领取与完成的事务骨架收在一处；校验与消息仍由阶段描述提供。"""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[sqlite3.Connection]],
        encode_results: Callable[[dict[str, Any]], str],
    ) -> None:
        """保存连接工厂与阶段消息的编码函数。"""
        self._connect = connect
        self._encode_results = encode_results

    def claim(
        self,
        spec: StageSpec,
        *,
        session_id: str,
        run_id: str,
        revision: int,
        attempt_id: str,
        values: dict[str, Any],
        validate: Callable[[sqlite3.Connection, sqlite3.Row], None],
        check_previous: Callable[[sqlite3.Row], None] | None = None,
        after_claim: Callable[[sqlite3.Connection, str], dict[str, Any]] | None = None,
        clock: Callable[[], str],
    ) -> dict[str, Any]:
        """短事务认领：相同匹配返回历史，否则校验后插入 running 并推进 revision。

        ``after_claim`` 可在同一事务内读回完整 attempt；``check_previous`` 可拒绝冲突历史。
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
            ).fetchone()
            if run is None:
                raise ValueError(spec.missing_run)
            match = {column: values[column] for column in spec.match_columns}
            where = " AND ".join(f"{column} = ?" for column in match)
            previous = conn.execute(
                f"SELECT * FROM {spec.table} WHERE run_id = ? AND {where}",
                (run_id, *match.values()),
            ).fetchone()
            if previous is not None:
                if check_previous is not None:
                    check_previous(previous)
                return {**dict(previous), "claimed": False}
            validate(conn, run)
            if run["revision"] != revision:
                raise ValueError(spec.revision_error)
            now = clock()
            columns = {
                "attempt_id": attempt_id,
                "run_id": run_id,
                **values,
                "status": "running",
                "created_at": now,
            }
            conn.execute(
                f"INSERT INTO {spec.table} ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(columns.values()),
            )
            advance_run(conn, run_id, session_id, now, touch_session=False)
            if after_claim is not None:
                return {**after_claim(conn, attempt_id), "claimed": True}
        return {"attempt_id": attempt_id, "directory": values["directory"], "claimed": True}

    def finish(
        self,
        spec: StageSpec,
        *,
        attempt_id: str,
        result: dict[str, Any],
        finalize: Callable[[sqlite3.Connection, sqlite3.Row, dict[str, Any]], dict[str, Any]]
        | None = None,
        clock: Callable[[], str],
    ) -> dict[str, Any]:
        """校验 running 后原子写终态、消息、revision 与会话时间，返回权威结果。

        ``finalize`` 只能读库并改写结果，不得写库或推进 revision。
        """
        if result["status"] not in spec.terminal:
            raise ValueError(spec.invalid_terminal)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute(
                f"SELECT a.*, r.session_id FROM {spec.table} a "
                "JOIN qc_runs r USING(run_id) WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt is None or attempt["status"] != "running":
                raise ValueError(spec.not_running)
            if finalize is not None:
                result = finalize(conn, attempt, result)
                if result["status"] not in spec.terminal:
                    raise ValueError(spec.invalid_terminal)
            now = clock()
            content, results = spec.message(attempt, result)
            message_id = publish_message(
                conn, attempt["session_id"], content, results, now, self._encode_results
            )
            conn.execute(
                f"UPDATE {spec.table} SET status = ?, finished_at = ?, result = ?, message_id = ? "
                "WHERE attempt_id = ? AND status = 'running'",
                (
                    result["status"],
                    now,
                    json.dumps(result, ensure_ascii=False),
                    message_id,
                    attempt_id,
                ),
            )
            advance_run(conn, attempt["run_id"], attempt["session_id"], now)
        return result
