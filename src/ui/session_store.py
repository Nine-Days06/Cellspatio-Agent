"""SQLite 会话存储：单库短连接 + WAL，逐次调用独立连接。"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(".cellspatio") / "sessions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT,
    summary_upto INTEGER NOT NULL DEFAULT 0,
    downloaded_assets TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    results TEXT,
    pending_script TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_results(results: dict[str, Any] | None) -> str | None:
    """results → JSON；plotly Figure 内嵌 json，matplotlib Figure 转 PNG base64。"""
    if results is None:
        return None
    payload: dict[str, Any] = dict(results)
    charts = payload.get("charts")
    if isinstance(charts, list):
        encoded = []
        for chart in charts:
            if hasattr(chart, "savefig"):            # matplotlib Figure
                import io

                buf = io.BytesIO()
                chart.savefig(buf, format="png", dpi=120, bbox_inches="tight")
                encoded.append({
                    "type": "image",
                    "image_b64": base64.b64encode(buf.getvalue()).decode(),
                })
            elif hasattr(chart, "to_json"):          # plotly Figure
                encoded.append({"type": "plotly", "figure_json": chart.to_json()})
            else:
                encoded.append(chart)                # 普通 dict 原样
        payload["charts"] = encoded
    return json.dumps(payload, ensure_ascii=False, default=str)


def _decode_results(text: str | None) -> dict[str, Any] | None:
    """JSON → results；plotly dict 惰性还原为 Figure，image dict 原样交给渲染层。"""
    if text is None:
        return None
    payload = json.loads(text)
    charts = payload.get("charts")
    if isinstance(charts, list):
        decoded = []
        for chart in charts:
            if isinstance(chart, dict) and chart.get("type") == "plotly":
                import plotly.io as pio

                decoded.append(pio.from_json(chart["figure_json"]))
            else:
                decoded.append(chart)
        payload["charts"] = decoded
    return payload


class SessionStore:
    """会话/消息 CRUD。每次操作独立短连接，不做连接池。"""

    def __init__(self, db_path: str | Path | None = None):
        path = db_path or os.environ.get("CELLSPATIO_SESSIONS_DB") or DEFAULT_DB_PATH
        self.db_path = Path(path)

    @contextmanager
    def _conn(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_session(self, title: str = "新会话") -> str:
        sid = uuid.uuid4().hex
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (sid, title, now, now),
            )
        return sid

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT s.id, s.title, s.updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) "
                "AS message_count FROM sessions s ORDER BY s.updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["downloaded_assets"] = _decode_downloaded(session["downloaded_assets"])
        return session

    def delete_session(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def append_message(self, session_id: str, role: str, content: str,
                       results: dict[str, Any] | None = None,
                       pending_script: dict[str, Any] | None = None) -> None:
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                "FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = row["next_seq"]
            conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, "
                "results, pending_script) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, seq, role, content, now,
                 _encode_results(results),
                 json.dumps(pending_script, ensure_ascii=False) if pending_script else None),
            )
            # 首条用户消息自动作为会话标题（截断 20 字）
            if seq == 1 and role == "user":
                conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (content[:20], now, session_id),
                )

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        messages = []
        for row in rows:
            msg = dict(row)
            msg["results"] = _decode_results(msg["results"])
            msg["pending_script"] = (
                json.loads(msg["pending_script"]) if msg["pending_script"] else None
            )
            messages.append(msg)
        return messages

    def update_session_meta(self, session_id: str, summary: str | None = None,
                            summary_upto: int | None = None,
                            downloaded_assets: list | None = None) -> None:
        """None = 不更新该字段；恒刷新 updated_at。"""
        sets, vals = [], []
        if summary is not None:
            sets.append("summary=?")
            vals.append(summary)
        if summary_upto is not None:
            sets.append("summary_upto=?")
            vals.append(summary_upto)
        if downloaded_assets is not None:
            sets.append("downloaded_assets=?")
            vals.append(json.dumps(downloaded_assets, ensure_ascii=False))
        sets.append("updated_at=?")
        vals.append(_now())
        vals.append(session_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", vals
            )

    def set_script_status(self, session_id: str, status: str) -> None:
        """仅更新该会话最新一条状态为 pending/expired 的脚本消息。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, pending_script FROM messages "
                "WHERE session_id = ? AND pending_script IS NOT NULL "
                "ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return
            script = json.loads(row["pending_script"])
            if script.get("status") not in ("pending", "expired"):
                return
            script["status"] = status
            conn.execute(
                "UPDATE messages SET pending_script = ? WHERE id = ?",
                (json.dumps(script, ensure_ascii=False), row["id"]),
            )


def _decode_downloaded(text: str | None) -> list:
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:  # 容错：坏数据按空列表处理
        logger.warning("downloaded_assets JSON 解析失败，按空列表处理")
        return []
