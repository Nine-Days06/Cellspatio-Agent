"""阶段表的 SQL 形状；事务边界由调用方持有。"""

import json
import sqlite3
from collections.abc import Mapping
from typing import Any


class StageRepository:
    """统一阶段 attempt 与复核事实的读写 SQL。"""

    def previous(
        self,
        conn: sqlite3.Connection,
        table: str,
        match: Mapping[str, Any],
        *,
        run_id: str | None = None,
    ) -> sqlite3.Row | None:
        """按唯一键查历史；带 run_id 时只在本次 run 内匹配。"""
        where = " AND ".join(f"{column} = ?" for column in match)
        values = tuple(match.values())
        if run_id is not None:
            where = f"run_id = ? AND {where}"
            values = (run_id, *values)
        return conn.execute(f"SELECT * FROM {table} WHERE {where}", values).fetchone()

    def latest(
        self, conn: sqlite3.Connection, table: str, match: Mapping[str, Any]
    ) -> sqlite3.Row | None:
        """按匹配键取最新一行，保持 rowid 倒序语义。"""
        where = " AND ".join(f"{column} = ?" for column in match)
        return conn.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY rowid DESC LIMIT 1",
            tuple(match.values()),
        ).fetchone()

    def insert(self, conn: sqlite3.Connection, table: str, values: Mapping[str, Any]) -> None:
        """插入一行；列集合完全由调用方决定。"""
        columns = ", ".join(values)
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )

    def running(self, conn: sqlite3.Connection, table: str, attempt_id: str) -> sqlite3.Row | None:
        """读取 running attempt 并附带所属会话。"""
        return conn.execute(
            f"SELECT a.*, r.session_id FROM {table} a "
            "JOIN qc_runs r USING(run_id) WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()

    def finish(
        self,
        conn: sqlite3.Connection,
        table: str,
        attempt_id: str,
        *,
        status: str,
        now: str,
        result: dict[str, Any],
        message_id: int,
    ) -> None:
        """只在 running 状态写入终态，保留原 CAS 守卫。"""
        conn.execute(
            f"UPDATE {table} SET status = ?, finished_at = ?, result = ?, message_id = ? "
            "WHERE attempt_id = ? AND status = 'running'",
            (status, now, json.dumps(result, ensure_ascii=False), message_id, attempt_id),
        )
