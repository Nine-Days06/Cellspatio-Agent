"""阶段 attempt 的通用领取与终态事务骨架；阶段差异由描述提供。

``append_review_fact`` 的执行顺序为：可选 ``validate``（``validate_first`` 时）→
``before_previous`` → previous 幂等/冲突 → ``validate``（默认）→ latest 去重 →
``before_insert`` → 消息 → 事实 INSERT → revision 推进。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from src.stage_defs import StageDefinition


@dataclass(frozen=True)
class StageSpec:
    """一个受控阶段的持久化描述：表、标签、终态、错误文案与消息形状。"""

    table: str
    label: str
    match_columns: tuple[str, ...]
    terminal: tuple[str, ...]
    missing_run: str
    revision_error: str
    invalid_terminal: str
    not_running: str
    definition: StageDefinition
    message: Callable[[sqlite3.Row, dict[str, Any]], tuple[str, dict[str, Any]]]
    interrupt_result: dict[str, Any] | None = None

    @property
    def directory_name(self) -> str:
        """阶段产物目录名；委托领域定义。"""
        return self.definition.directory_name

    @property
    def failure_stage(self) -> str:
        """失败响应中的阶段名；委托领域定义。"""
        return self.definition.failure_stage


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


@dataclass(frozen=True)
class ReviewFactPlan:
    """一次人工复核事实的持久化描述；阶段语义只存在于回调与文案。

    ``previous_match`` / ``latest_match`` 是 WHERE 列值映射，标识符只能来自内部阶段描述；
    ``insert_values`` 不含接缝生成的 review_id/created_at/message_id。
    ``latest_match`` 与 ``same_latest`` 必须成对提供；latest 去重要求插入值含
    ``source_revision``。消息与 run 都依赖 ``session_id``。
    """

    table: str
    previous_match: Mapping[str, Any]
    insert_values: Mapping[str, Any]
    same_previous: Callable[[sqlite3.Row], bool]
    conflict_message: str
    latest_match: Mapping[str, Any] | None = None
    same_latest: Callable[[sqlite3.Row], bool] | None = None
    message: Callable[[dict[str, Any]], tuple[str, dict[str, Any]]] | None = None
    run_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        """拒绝会静默降级或无法绑定会话的无效组合。"""
        if (self.latest_match is None) != (self.same_latest is None):
            raise ValueError("latest_match 与 same_latest 必须同时提供")
        if self.latest_match is not None and "source_revision" not in self.insert_values:
            raise ValueError("latest 去重需要 insert_values.source_revision")
        if self.session_id is None and (self.run_id is not None or self.message is not None):
            raise ValueError("复核 run 或消息需要 session_id")


@dataclass(frozen=True)
class ReviewFactResult:
    """接缝结果：是否新增，以及命中的事实行。"""

    inserted: bool
    fact: dict[str, Any]


def append_review_fact(
    connect: Callable[[], AbstractContextManager[sqlite3.Connection]],
    encode_results: Callable[[dict[str, Any]], str],
    plan: ReviewFactPlan,
    *,
    before_previous: Callable[[sqlite3.Connection], None] | None = None,
    validate: Callable[[sqlite3.Connection], None] | None = None,
    before_insert: Callable[[sqlite3.Connection], None] | None = None,
    validate_first: bool = False,
    clock: Callable[[], str],
) -> ReviewFactResult:
    """在单一事务内追加复核事实：previous 幂等/冲突后插入，返回是否新增。

    顺序：可选 ``validate``（``validate_first`` 时）→ ``before_previous`` → previous
    幂等/冲突 → ``validate``（默认）→ latest 去重 → ``before_insert`` → 消息 →
    INSERT → revision。``before_previous`` 在幂等早退前检查始终存在的前提；
    ``validate`` 负责 run/父/attempt 的存在与身份校验；``validate_first`` 供
    “旧 revision 先报失效”的路径使用，必须同时提供 ``validate``。
    """
    if validate_first and validate is None:
        raise ValueError("validate_first 需要 validate")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if validate_first and validate is not None:
            validate(conn)
        if before_previous is not None:
            before_previous(conn)
        where = " AND ".join(f"{column} = ?" for column in plan.previous_match)
        previous = conn.execute(
            f"SELECT * FROM {plan.table} WHERE {where}", tuple(plan.previous_match.values())
        ).fetchone()
        if previous is not None:
            if not plan.same_previous(previous):
                raise ValueError(plan.conflict_message)
            return ReviewFactResult(False, dict(previous))
        if not validate_first and validate is not None:
            validate(conn)
        if plan.latest_match is not None and plan.same_latest is not None:
            latest_where = " AND ".join(f"{column} = ?" for column in plan.latest_match)
            latest = conn.execute(
                f"SELECT * FROM {plan.table} WHERE {latest_where} ORDER BY rowid DESC LIMIT 1",
                tuple(plan.latest_match.values()),
            ).fetchone()
            new_revision = plan.insert_values.get("source_revision")
            if (
                latest is not None
                and new_revision is not None
                and latest["source_revision"] + 1 == new_revision
                and plan.same_latest(latest)
            ):
                return ReviewFactResult(False, dict(latest))
        if before_insert is not None:
            before_insert(conn)
        now = clock()
        fact = {**plan.insert_values}
        fact.setdefault("review_id", uuid.uuid4().hex)
        fact.setdefault("created_at", now)
        if plan.message is not None:
            content, results = plan.message(fact)
            fact["message_id"] = publish_message(
                conn, plan.session_id, content, results, now, encode_results
            )
        columns = ", ".join(fact)
        conn.execute(
            f"INSERT INTO {plan.table} ({columns}) VALUES ({', '.join('?' for _ in fact)})",
            tuple(fact.values()),
        )
        if plan.run_id is not None:
            advance_run(conn, plan.run_id, plan.session_id, now)
        return ReviewFactResult(True, fact)


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
