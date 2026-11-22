"""阶段 repository 的 SQL 形状与 CAS 守卫。"""

import json
import sqlite3
from contextlib import closing

from src.ui.stage_repository import StageRepository


def _conn() -> sqlite3.Connection:
    """内存库：qc_runs 加最小阶段表。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE qc_runs (run_id TEXT PRIMARY KEY, session_id TEXT);
        CREATE TABLE stage (
            attempt_id TEXT PRIMARY KEY, run_id TEXT, status TEXT,
            worker TEXT, finished_at TEXT, result TEXT, message_id INTEGER
        );
        INSERT INTO qc_runs VALUES ('r1', 's1');
        """
    )
    return conn


def test_previous_scopes_to_run_and_latest_uses_rowid() -> None:
    """previous 可按 run 收窄，latest 取最后插入。"""
    with closing(_conn()) as conn:
        conn.execute("INSERT INTO stage VALUES ('a1', 'r1', 'running', 'w', NULL, NULL, NULL)")
        conn.execute("INSERT INTO stage VALUES ('a2', 'r1', 'running', 'w', NULL, NULL, NULL)")
        conn.execute("INSERT INTO stage VALUES ('a3', 'r2', 'running', 'w', NULL, NULL, NULL)")
        repo = StageRepository()

        assert repo.previous(conn, "stage", {"worker": "w"})["attempt_id"] == "a1"
        assert repo.previous(conn, "stage", {"worker": "w"}, run_id="r2")["attempt_id"] == "a3"
        assert repo.latest(conn, "stage", {"worker": "w"})["attempt_id"] == "a3"


def test_insert_running_and_finish_keep_running_guard() -> None:
    """插入列集合原样落库，终态更新只命中 running。"""
    with closing(_conn()) as conn:
        repo = StageRepository()
        repo.insert(
            conn,
            "stage",
            {"attempt_id": "a1", "run_id": "r1", "status": "running", "worker": "w"},
        )
        assert repo.running(conn, "stage", "a1")["session_id"] == "s1"
        repo.finish(
            conn,
            "stage",
            "a1",
            status="completed",
            now="now",
            result={"status": "completed"},
            message_id=9,
        )
        row = conn.execute("SELECT * FROM stage WHERE attempt_id = 'a1'").fetchone()
        assert (row["status"], row["finished_at"], row["message_id"]) == ("completed", "now", 9)
        assert json.loads(row["result"]) == {"status": "completed"}
        assert repo.running(conn, "stage", "a1")["status"] == "completed"

        repo.finish(
            conn,
            "stage",
            "a1",
            status="failed",
            now="later",
            result={"status": "failed"},
            message_id=10,
        )
        assert conn.execute("SELECT status FROM stage WHERE attempt_id = 'a1'").fetchone()[0] == (
            "completed"
        )
