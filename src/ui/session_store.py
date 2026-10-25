"""SQLite 会话存储：单库短连接 + WAL，逐次调用独立连接。"""

from __future__ import annotations

import base64
import io
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
    pending_script TEXT,
    -- 短连接模型下 append_message 串行取 MAX(seq)+1，唯一约束兜底防并发串号
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE TABLE IF NOT EXISTS qc_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    directory TEXT NOT NULL UNIQUE,
    evidence TEXT NOT NULL,
    active_preview_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS qc_previews (
    preview_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES qc_runs(run_id) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    approved_at TEXT,
    reason TEXT,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS sc_filter_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES qc_runs(run_id) ON DELETE CASCADE,
    preview_id TEXT NOT NULL REFERENCES qc_previews(preview_id) ON DELETE CASCADE,
    binding_sha256 TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    directory TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(run_id, preview_id, binding_sha256, tool_version)
);
CREATE TABLE IF NOT EXISTS sc_pca_attempts (
    attempt_id TEXT PRIMARY KEY,
    filter_attempt_id TEXT NOT NULL REFERENCES sc_filter_attempts(attempt_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES qc_runs(run_id) ON DELETE CASCADE,
    binding_sha256 TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    protocol TEXT NOT NULL,
    reason TEXT NOT NULL,
    directory TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(filter_attempt_id, binding_sha256, tool_version)
);
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
            if hasattr(chart, "savefig"):  # matplotlib Figure
                buf = io.BytesIO()
                chart.savefig(buf, format="png", dpi=120, bbox_inches="tight")
                encoded.append(
                    {
                        "type": "image",
                        "image_b64": base64.b64encode(buf.getvalue()).decode(),
                    }
                )
            elif hasattr(chart, "to_json"):  # plotly Figure
                encoded.append({"type": "plotly", "figure_json": chart.to_json()})
            else:
                encoded.append(chart)  # 普通 dict 原样
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
                "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
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
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["downloaded_assets"] = _decode_downloaded(session["downloaded_assets"])
        return session

    def delete_session(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM sc_filter_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE r.session_id = ? AND a.status = 'running'",
                (session_id,),
            ).fetchone():
                raise ValueError("过滤运行中，不能删除会话")
            if conn.execute(
                "SELECT 1 FROM sc_pca_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE r.session_id = ? AND a.status = 'running'",
                (session_id,),
            ).fetchone():
                raise ValueError("PCA 运行中，不能删除会话")
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        results: dict[str, Any] | None = None,
        pending_script: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = row["next_seq"]
            conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, "
                "results, pending_script) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    seq,
                    role,
                    content,
                    now,
                    _encode_results(results),
                    json.dumps(pending_script, ensure_ascii=False) if pending_script else None,
                ),
            )
            # 首条用户消息自动作为会话标题（截断 20 字）
            if seq == 1 and role == "user":
                conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (content[:20], now, session_id),
                )

    def register_qc_run(
        self, session_id: str, run_id: str, directory: str, evidence: dict[str, Any]
    ) -> None:
        """认领受控层校验过的 QC；唯一约束禁止跨会话补认领。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO qc_runs (run_id, session_id, directory, evidence) VALUES (?, ?, ?, ?)",
                (run_id, session_id, directory, json.dumps(evidence, ensure_ascii=False)),
            )

    def get_qc_run(self, session_id: str, run_id: str) -> dict[str, Any] | None:
        """读取权威 run 与预览/批准历史，不从消息倒推状态。"""
        with self._conn() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
            ).fetchone()
            if row is None:
                return None
            previews = conn.execute(
                "SELECT * FROM qc_previews WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
            attempts = conn.execute(
                "SELECT * FROM sc_filter_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
            pca_attempts = conn.execute(
                "SELECT * FROM sc_pca_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
        run = dict(row)
        run["evidence"] = json.loads(run["evidence"])
        run["previews"] = [
            {
                **json.loads(p["payload"]),
                "status": p["status"],
                "approved_at": p["approved_at"],
                "reason": p["reason"],
                "message_id": p["message_id"],
            }
            for p in previews
        ]
        run["attempts"] = [
            {**dict(a), "result": json.loads(a["result"]) if a["result"] else None}
            for a in attempts
        ]
        run["pca_attempts"] = [
            {**dict(a), "result": json.loads(a["result"]) if a["result"] else None}
            for a in pca_attempts
        ]
        return run

    def activate_qc_preview(
        self, session_id: str, run_id: str, revision: int, preview: dict[str, Any]
    ) -> None:
        """短事务条件激活；哈希计算期间的新预览/批准使旧请求冲突。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_no_filter(conn, run_id)
            changed = conn.execute(
                "UPDATE qc_runs SET active_preview_id = ?, revision = revision + 1 "
                "WHERE session_id = ? AND run_id = ? AND revision = ?",
                (preview["preview_id"], session_id, run_id, revision),
            ).rowcount
            if changed != 1:
                raise ValueError("QC 状态已变化，请刷新后重新预览")
            conn.execute(
                "INSERT INTO qc_previews (preview_id, run_id, payload) VALUES (?, ?, ?)",
                (preview["preview_id"], run_id, json.dumps(preview, ensure_ascii=False)),
            )

    def approve_qc_preview(
        self, session_id: str, run_id: str, revision: int, preview_id: str, reason: str
    ) -> dict[str, Any]:
        """批准事实与一条展示消息原子落库；重复请求返回原事实。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_no_filter(conn, run_id)
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
            ).fetchone()
            preview = conn.execute(
                "SELECT * FROM qc_previews WHERE run_id = ? AND preview_id = ?",
                (run_id, preview_id),
            ).fetchone()
            if run is None or preview is None:
                raise ValueError("QC 关联不存在")
            if preview["status"] != "approved":
                if run["revision"] != revision or run["active_preview_id"] != preview_id:
                    raise ValueError("预览已失效，请刷新后重新预览")
                payload = json.loads(preview["payload"])
                if payload["summary"]["blocked"]:
                    raise ValueError("该预览不能批准")
                now = _now()
                seq = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                message_id = conn.execute(
                    "INSERT INTO messages (session_id, seq, role, content, timestamp, results) "
                    "VALUES (?, ?, 'assistant', ?, ?, ?)",
                    (
                        session_id,
                        seq,
                        "过滤选择已记录，尚未执行过滤或下游分析。",
                        now,
                        _encode_results(
                            {"qc_selection": {"run_id": run_id, "preview_id": preview_id}}
                        ),
                    ),
                ).lastrowid
                conn.execute(
                    "UPDATE qc_previews SET status = 'approved', approved_at = ?, reason = ?, message_id = ? "
                    "WHERE preview_id = ? AND status = 'pending'",
                    (now, reason, message_id, preview_id),
                )
                conn.execute(
                    "UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run_id,)
                )
                conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return self.get_qc_run(session_id, run_id)

    @staticmethod
    def _require_no_filter(conn: sqlite3.Connection, run_id: str) -> None:
        """运行中的选择不能被新预览或批准替换。"""
        if conn.execute(
            "SELECT 1 FROM sc_filter_attempts WHERE run_id = ? AND status = 'running'", (run_id,)
        ).fetchone():
            raise ValueError("过滤运行中，不能修改选择")
        if conn.execute(
            "SELECT 1 FROM sc_pca_attempts WHERE run_id = ? AND status = 'running'", (run_id,)
        ).fetchone():
            raise ValueError("PCA 运行中，不能修改选择")

    def start_sc_filter(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        preview_id: str,
        binding: str,
        tool_version: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """短事务认领当前已批准选择；相同绑定只执行一次。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
            ).fetchone()
            if run is None:
                raise ValueError("QC 关联不存在")
            previous = conn.execute(
                "SELECT * FROM sc_filter_attempts WHERE run_id = ? AND preview_id = ? "
                "AND binding_sha256 = ? AND tool_version = ?",
                (run_id, preview_id, binding, tool_version),
            ).fetchone()
            if previous:
                return {**dict(previous), "claimed": False}
            self._require_no_filter(conn, run_id)
            preview = conn.execute(
                "SELECT * FROM qc_previews WHERE run_id = ? AND preview_id = ?",
                (run_id, preview_id),
            ).fetchone()
            if (
                run["revision"] != revision
                or run["active_preview_id"] != preview_id
                or preview is None
                or preview["status"] != "approved"
                or json.loads(preview["payload"])["binding_sha256"] != binding
            ):
                raise ValueError("仅可执行当前已批准的精确选择，请刷新")
            conn.execute(
                "INSERT INTO sc_filter_attempts "
                "(attempt_id, run_id, preview_id, binding_sha256, tool_version, directory, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
                (attempt_id, run_id, preview_id, binding, tool_version, directory, _now()),
            )
            conn.execute("UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run_id,))
        return {"attempt_id": attempt_id, "directory": directory, "claimed": True}

    def finish_sc_filter(self, attempt_id: str, result: dict[str, Any]) -> None:
        """技术终态与单条阶段消息原子落库，不暴露每细胞证据。"""
        if result["status"] not in ("completed", "failed"):
            raise ValueError("过滤终态非法")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute(
                "SELECT a.*, r.session_id FROM sc_filter_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt is None or attempt["status"] != "running":
                raise ValueError("过滤任务不在运行状态")
            sid, now = attempt["session_id"], _now()
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?",
                (sid,),
            ).fetchone()[0]
            content = (
                "过滤产物已保存，等待审阅；科学未复核，未执行归一化或聚类。"
                if result["status"] == "completed"
                else "过滤失败，本地证据已保留；不会自动重跑。"
            )
            message_id = conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, results) "
                "VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    sid,
                    seq,
                    content,
                    now,
                    _encode_results(
                        {
                            "sc_filter": {
                                "run_id": attempt["run_id"],
                                "attempt_id": attempt_id,
                                "stage": "filter_review",
                                "technical_status": result["status"],
                                "scientific_status": "unreviewed",
                            }
                        }
                    ),
                ),
            ).lastrowid
            conn.execute(
                "UPDATE sc_filter_attempts SET status = ?, finished_at = ?, result = ?, message_id = ? "
                "WHERE attempt_id = ? AND status = 'running'",
                (
                    result["status"],
                    now,
                    json.dumps(result, ensure_ascii=False),
                    message_id,
                    attempt_id,
                ),
            )
            conn.execute(
                "UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (attempt["run_id"],)
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, sid))

    def interrupt_sc_filters(self) -> None:
        """仅服务启动恢复：中断历史 running，不接管、不自动重跑。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE sc_filter_attempts SET status = 'interrupted', finished_at = ? "
                "WHERE status = 'running'",
                (_now(),),
            )

    def start_sc_pca(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        filter_attempt_id: str,
        binding: str,
        tool_version: str,
        protocol: dict[str, Any],
        reason: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """短事务认领独立授权；哈希期间的选择变化通过 revision 拒绝。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
            ).fetchone()
            if run is None:
                raise ValueError("QC 关联不存在")
            previous = conn.execute(
                "SELECT * FROM sc_pca_attempts WHERE filter_attempt_id = ? "
                "AND binding_sha256 = ? AND tool_version = ?",
                (filter_attempt_id, binding, tool_version),
            ).fetchone()
            if previous:
                return {**dict(previous), "claimed": False}
            self._require_no_filter(conn, run_id)
            parent = conn.execute(
                "SELECT a.*, p.status AS approval FROM sc_filter_attempts a "
                "JOIN qc_previews p USING(preview_id) WHERE a.attempt_id = ? AND a.run_id = ?",
                (filter_attempt_id, run_id),
            ).fetchone()
            if (
                run["revision"] != revision
                or parent is None
                or parent["status"] != "completed"
                or parent["approval"] != "approved"
                or run["active_preview_id"] != parent["preview_id"]
            ):
                raise ValueError("仅可授权当前已批准且过滤完成的父阶段，请刷新")
            conn.execute(
                "INSERT INTO sc_pca_attempts (attempt_id, filter_attempt_id, run_id, binding_sha256, "
                "tool_version, protocol, reason, directory, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
                (
                    attempt_id,
                    filter_attempt_id,
                    run_id,
                    binding,
                    tool_version,
                    json.dumps(protocol, ensure_ascii=False),
                    reason,
                    directory,
                    _now(),
                ),
            )
            conn.execute("UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run_id,))
        return {"attempt_id": attempt_id, "directory": directory, "claimed": True}

    def finish_sc_pca(self, attempt_id: str, result: dict[str, Any]) -> None:
        """终态和安全阶段消息原子保存；私有分析证据不进入聊天。"""
        if result["status"] not in ("completed", "failed"):
            raise ValueError("PCA 终态非法")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute(
                "SELECT a.*, r.session_id FROM sc_pca_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt is None or attempt["status"] != "running":
                raise ValueError("PCA 任务不在运行状态")
            sid, now = attempt["session_id"], _now()
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?", (sid,)
            ).fetchone()[0]
            message_id = conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, results) "
                "VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    sid,
                    seq,
                    "归一化与 PCA 产物已保存，等待审阅；科学未复核，未执行聚类或注释。"
                    if result["status"] == "completed"
                    else "归一化与 PCA 失败，本地证据保留；不会自动重跑。",
                    now,
                    _encode_results(
                        {
                            "sc_pca": {
                                "run_id": attempt["run_id"],
                                "filter_attempt_id": attempt["filter_attempt_id"],
                                "attempt_id": attempt_id,
                                "stage": "pca_review",
                                "technical_status": result["status"],
                                "scientific_status": "unreviewed",
                            }
                        }
                    ),
                ),
            ).lastrowid
            conn.execute(
                "UPDATE sc_pca_attempts SET status = ?, finished_at = ?, result = ?, message_id = ? "
                "WHERE attempt_id = ? AND status = 'running'",
                (
                    result["status"],
                    now,
                    json.dumps(result, ensure_ascii=False),
                    message_id,
                    attempt_id,
                ),
            )
            conn.execute(
                "UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (attempt["run_id"],)
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, sid))

    def interrupt_sc_pcas(self) -> None:
        """单进程启动恢复：旧 running 标中断，不接管或重跑。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE sc_pca_attempts SET status = 'interrupted', finished_at = ? "
                "WHERE status = 'running'",
                (_now(),),
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

    def update_session_meta(
        self,
        session_id: str,
        summary: str | None = None,
        summary_upto: int | None = None,
        downloaded_assets: list | None = None,
    ) -> None:
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
            conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", vals)

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
