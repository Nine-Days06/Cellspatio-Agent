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
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'local',
    root_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    summary TEXT,
    summary_upto INTEGER NOT NULL DEFAULT 0,
    downloaded_assets TEXT NOT NULL DEFAULT '[]',
    project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT
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
CREATE TABLE IF NOT EXISTS sc_pca_dimension_reviews (
    review_id TEXT PRIMARY KEY,
    pca_attempt_id TEXT NOT NULL REFERENCES sc_pca_attempts(attempt_id) ON DELETE CASCADE,
    binding_sha256 TEXT NOT NULL,
    record_version TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    dims TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(pca_attempt_id, binding_sha256, record_version, source_revision)
);
CREATE TABLE IF NOT EXISTS sc_cluster_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES qc_runs(run_id) ON DELETE CASCADE,
    pca_attempt_id TEXT NOT NULL REFERENCES sc_pca_attempts(attempt_id) ON DELETE CASCADE,
    review_id TEXT NOT NULL REFERENCES sc_pca_dimension_reviews(review_id) ON DELETE CASCADE,
    snapshot TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    protocol TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    claim_revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    directory TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(pca_attempt_id, binding_sha256, tool_version)
);
CREATE TABLE IF NOT EXISTS sc_cluster_reviews (
    review_id TEXT PRIMARY KEY,
    cluster_attempt_id TEXT NOT NULL REFERENCES sc_cluster_attempts(attempt_id) ON DELETE CASCADE,
    binding_sha256 TEXT NOT NULL,
    record_version TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    accepted INTEGER NOT NULL CHECK(accepted = 1),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(cluster_attempt_id, binding_sha256, record_version, source_revision)
);
CREATE TABLE IF NOT EXISTS sc_marker_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES qc_runs(run_id) ON DELETE CASCADE,
    cluster_attempt_id TEXT NOT NULL REFERENCES sc_cluster_attempts(attempt_id) ON DELETE CASCADE,
    review_id TEXT NOT NULL REFERENCES sc_cluster_reviews(review_id) ON DELETE CASCADE,
    snapshot TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    protocol TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    claim_revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    directory TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(cluster_attempt_id, binding_sha256, tool_version)
);
CREATE TABLE IF NOT EXISTS knowledge_qa_reviews (
    review_id TEXT PRIMARY KEY,
    answer_id TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL,
    record_version TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
    veto INTEGER NOT NULL DEFAULT 0 CHECK(veto IN (0, 1)),
    reason TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(answer_id, binding_sha256, record_version, source_revision)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_qa_reviews_answer
    ON knowledge_qa_reviews(answer_id, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """旧库补列并建索引：CREATE TABLE IF NOT EXISTS 不会修改既有 sessions 表。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    additions = {
        "project_id": "TEXT REFERENCES projects(id) ON DELETE RESTRICT",
        "pinned": "INTEGER NOT NULL DEFAULT 0",
        "archived_at": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at)")


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
            _migrate_schema(conn)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_session(self, title: str = "新会话", project_id: str | None = None) -> str:
        if project_id is not None:
            project = self.get_project(project_id)
            if project is None:
                raise ValueError("项目不存在")
            if project["archived_at"]:
                raise ValueError("项目已归档")
        sid = uuid.uuid4().hex
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at, project_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, title, now, now, project_id),
            )
            if project_id is not None:
                conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return sid

    def list_sessions(self, include_archived: bool = False) -> list[dict[str, Any]]:
        where = (
            ""
            if include_archived
            else "WHERE s.archived_at IS NULL AND (s.project_id IS NULL OR p.archived_at IS NULL)"
        )
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT s.id, s.title, s.updated_at, s.project_id, s.pinned, "
                "s.archived_at, p.label AS project_label, "
                "(SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) "
                f"AS message_count FROM sessions s LEFT JOIN projects p "
                f"ON p.id = s.project_id {where} "
                "ORDER BY s.pinned DESC, s.updated_at DESC"
            ).fetchall()
        sessions = [dict(r) for r in rows]
        for session in sessions:
            session["pinned"] = bool(session["pinned"])
        return sessions

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["downloaded_assets"] = _decode_downloaded(session["downloaded_assets"])
        session["pinned"] = bool(session["pinned"])
        return session

    def create_project(self, label: str, root_path: str | None = None, kind: str = "local") -> str:
        """创建扁平项目；同名允许，靠 id 区分。"""
        pid = uuid.uuid4().hex
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO projects (id, label, kind, root_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pid, label, kind, root_path, now, now),
            )
        return pid

    def list_projects(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """列出项目及未归档会话数；默认隐藏已归档。"""
        where = "" if include_archived else "WHERE p.archived_at IS NULL"
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM sessions s "
                "WHERE s.project_id = p.id AND s.archived_at IS NULL) AS session_count "
                f"FROM projects p {where} ORDER BY p.updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """按 id 取项目（含未归档会话数）；不存在返回 None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM sessions s "
                "WHERE s.project_id = p.id AND s.archived_at IS NULL) AS session_count "
                "FROM projects p WHERE p.id = ?",
                (project_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_project(
        self,
        project_id: str,
        label: str | None = None,
        root_path: str | None = None,
        clear_root_path: bool = False,
        archived: bool | None = None,
    ) -> None:
        """None = 不更新；archived 显式切换归档时间。"""
        if archived:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT id FROM sessions WHERE project_id=? AND archived_at IS NULL",
                    (project_id,),
                ).fetchall()
            for row in rows:
                running = self.running_stage(row["id"])
                if running:
                    raise ValueError(f"{running}运行中，不能归档项目")
        sets, vals = [], []
        if label is not None:
            sets.append("label=?")
            vals.append(label)
        if root_path is not None or clear_root_path:
            sets.append("root_path=?")
            vals.append(root_path or None)
        if archived is not None:
            sets.append("archived_at=?")
            vals.append(_now() if archived else None)
        if not sets:
            return
        sets.append("updated_at=?")
        vals.extend([_now(), project_id])
        with self._conn() as conn:
            cursor = conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", vals)
            if cursor.rowcount == 0:
                raise ValueError("项目不存在")

    def delete_project(self, project_id: str) -> None:
        """只删除空项目；非空项目须先移动或归档会话。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM sessions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if row["total"]:
                raise ValueError("项目仍有会话，不能删除")
            cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            if cursor.rowcount == 0:
                raise ValueError("项目不存在")

    def move_session(self, session_id: str, project_id: str | None) -> None:
        """移动到项目；None = 移回未归类。"""
        if project_id is not None:
            project = self.get_project(project_id)
            if project is None:
                raise ValueError("项目不存在")
            if project["archived_at"]:
                raise ValueError("项目已归档")
        with self._conn() as conn:
            previous = conn.execute(
                "SELECT project_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            cursor = conn.execute(
                "UPDATE sessions SET project_id=?, updated_at=? WHERE id=?",
                (project_id, _now(), session_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("会话不存在")
            for touched in {previous["project_id"], project_id} - {None}:
                conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (_now(), touched))

    def set_session_pinned(self, session_id: str, pinned: bool) -> None:
        """设置会话置顶状态。"""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET pinned=?, updated_at=? WHERE id=?",
                (1 if pinned else 0, _now(), session_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("会话不存在")

    def archive_session(self, session_id: str, archived: bool = True) -> None:
        """归档或恢复会话；运行中的受控阶段拒绝归档。"""
        if archived:
            running = self.running_stage(session_id)
            if running:
                raise ValueError(f"{running}运行中，不能归档会话")
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET archived_at=?, updated_at=? WHERE id=?",
                (_now() if archived else None, _now(), session_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("会话不存在")

    def delete_session(self, session_id: str) -> None:
        """删除会话及其消息；运行中的受控阶段拒绝删除。"""
        running = self.running_stage(session_id)
        if running:
            raise ValueError(f"{running}运行中，不能删除会话")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def running_stage(self, session_id: str) -> str | None:
        """返回正在运行的受控阶段名；供删除/归档前预检。"""
        checks = (
            (
                "过滤",
                "SELECT 1 FROM sc_filter_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE r.session_id=? AND a.status='running'",
            ),
            (
                "PCA",
                "SELECT 1 FROM sc_pca_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE r.session_id=? AND a.status='running'",
            ),
            (
                "聚类",
                "SELECT 1 FROM sc_cluster_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE r.session_id=? AND a.status='running'",
            ),
            (
                "marker",
                "SELECT 1 FROM sc_marker_attempts a JOIN qc_runs r USING(run_id) "
                "WHERE r.session_id=? AND a.status='running'",
            ),
        )
        with self._conn() as conn:
            for label, sql in checks:
                if conn.execute(sql, (session_id,)).fetchone():
                    return label
        return None

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
            else:
                conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            conn.execute(
                "UPDATE projects SET updated_at = ? "
                "WHERE id = (SELECT project_id FROM sessions WHERE id = ?)",
                (now, session_id),
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
            dimension_reviews = conn.execute(
                "SELECT d.* FROM sc_pca_dimension_reviews d JOIN sc_pca_attempts a "
                "ON d.pca_attempt_id = a.attempt_id WHERE a.run_id = ? ORDER BY d.rowid",
                (run_id,),
            ).fetchall()
            cluster_attempts = conn.execute(
                "SELECT * FROM sc_cluster_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
            cluster_reviews = conn.execute(
                "SELECT d.* FROM sc_cluster_reviews d JOIN sc_cluster_attempts a "
                "ON d.cluster_attempt_id = a.attempt_id WHERE a.run_id = ? ORDER BY d.rowid",
                (run_id,),
            ).fetchall()
            marker_attempts = conn.execute(
                "SELECT * FROM sc_marker_attempts WHERE run_id = ? ORDER BY rowid", (run_id,)
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
        run["dimension_reviews"] = [
            {**dict(d), "dims": json.loads(d["dims"])} for d in dimension_reviews
        ]
        run["cluster_attempts"] = [
            {
                **dict(a),
                "snapshot": json.loads(a["snapshot"]),
                "result": json.loads(a["result"]) if a["result"] else None,
            }
            for a in cluster_attempts
        ]
        run["cluster_reviews"] = [
            {**dict(d), "accepted": bool(d["accepted"])} for d in cluster_reviews
        ]
        run["marker_attempts"] = [
            {
                **dict(a),
                "snapshot": json.loads(a["snapshot"]),
                "result": json.loads(a["result"]) if a["result"] else None,
            }
            for a in marker_attempts
        ]
        return run

    def list_qc_runs(self, session_id: str) -> list[dict[str, Any]]:
        """按创建顺序列出会话的 QC run（供导出/删除 CLI 枚举产物）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id=? ORDER BY rowid", (session_id,)
            ).fetchall()
        return [dict(row) for row in rows]

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
        if conn.execute(
            "SELECT 1 FROM sc_cluster_attempts WHERE run_id = ? AND status = 'running'", (run_id,)
        ).fetchone():
            raise ValueError("聚类运行中，不能修改选择")
        if conn.execute(
            "SELECT 1 FROM sc_marker_attempts WHERE run_id = ? AND status = 'running'", (run_id,)
        ).fetchone():
            raise ValueError("marker 运行中，不能修改选择")

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

    def record_pca_dimensions(
        self,
        session_id: str,
        run: dict[str, Any],
        parent: dict[str, Any],
        attempt: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        dims: list[int],
        reason: str,
    ) -> None:
        """追加人工事实与安全消息；CAS 防止校验期间发生选择变化。"""
        # shortcut: 文件哈希在事务外检查，本地证据可被外部改写；生产隔离另片处理。
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?",
                (session_id, run["run_id"]),
            ).fetchone()
            if row is None:
                raise ValueError("QC 关联不存在")
            previous = conn.execute(
                "SELECT * FROM sc_pca_dimension_reviews WHERE pca_attempt_id = ? "
                "AND binding_sha256 = ? AND record_version = ? AND source_revision = ?",
                (attempt["attempt_id"], binding, version, revision),
            ).fetchone()
            if previous:
                if json.loads(previous["dims"]) != dims or previous["reason"] != reason:
                    raise ValueError("同一 revision 的记录内容冲突，请刷新")
                return
            self._require_no_filter(conn, run["run_id"])
            live_parent = conn.execute(
                "SELECT a.*, p.status AS approval FROM sc_filter_attempts a "
                "JOIN qc_previews p USING(preview_id) WHERE a.attempt_id = ? AND a.run_id = ?",
                (parent["attempt_id"], run["run_id"]),
            ).fetchone()
            live_pca = conn.execute(
                "SELECT * FROM sc_pca_attempts WHERE attempt_id = ? AND run_id = ?",
                (attempt["attempt_id"], run["run_id"]),
            ).fetchone()
            if (
                row["revision"] != revision
                or json.loads(row["evidence"]) != run["evidence"]
                or live_parent is None
                or live_parent["status"] != "completed"
                or live_parent["approval"] != "approved"
                or row["active_preview_id"] != parent["preview_id"]
                or any(
                    live_parent[k] != parent[k]
                    for k in ("binding_sha256", "tool_version", "directory")
                )
                or json.loads(live_parent["result"]) != parent["result"]
                or live_pca is None
                or live_pca["status"] != "completed"
                or any(
                    live_pca[k] != attempt[k]
                    for k in (
                        "filter_attempt_id",
                        "binding_sha256",
                        "tool_version",
                        "protocol",
                        "directory",
                    )
                )
                or json.loads(live_pca["result"]) != attempt["result"]
            ):
                raise ValueError("PCA 或父选择已变化，请刷新后重新记录")
            latest = conn.execute(
                "SELECT * FROM sc_pca_dimension_reviews WHERE pca_attempt_id = ? ORDER BY rowid DESC LIMIT 1",
                (attempt["attempt_id"],),
            ).fetchone()
            if (
                latest
                and latest["binding_sha256"] == binding
                and latest["record_version"] == version
                and latest["source_revision"] + 1 == revision
                and json.loads(latest["dims"]) == dims
                and latest["reason"] == reason
            ):
                return
            review_id, now = uuid.uuid4().hex, _now()
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            message_id = conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, results) "
                "VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    session_id,
                    seq,
                    "维度选择已记录，科学未复核；未授权下游执行。",
                    now,
                    _encode_results(
                        {
                            "pca_dimensions": {
                                "run_id": run["run_id"],
                                "filter_attempt_id": parent["attempt_id"],
                                "attempt_id": attempt["attempt_id"],
                                "review_id": review_id,
                                "stage": "pca_review",
                                "scientific_status": "unreviewed",
                            }
                        }
                    ),
                ),
            ).lastrowid
            conn.execute(
                "INSERT INTO sc_pca_dimension_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    attempt["attempt_id"],
                    binding,
                    version,
                    revision,
                    json.dumps(dims),
                    reason,
                    now,
                    message_id,
                ),
            )
            conn.execute(
                "UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run["run_id"],)
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def interrupt_sc_pcas(self) -> None:
        """单进程启动恢复：旧 running 标中断，不接管或重跑。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE sc_pca_attempts SET status = 'interrupted', finished_at = ? "
                "WHERE status = 'running'",
                (_now(),),
            )

    @staticmethod
    def _cluster_source_matches(
        conn: sqlite3.Connection, run: sqlite3.Row, snapshot: dict[str, Any]
    ) -> bool:
        """只比较认领快照，不把本次 revision 推进当上游失效。"""
        if json.loads(run["evidence"]) != snapshot["evidence"]:
            return False
        parent, pca, review = snapshot["parent"], snapshot["pca"], snapshot["review"]
        live_parent = conn.execute(
            "SELECT a.*, p.status AS approval FROM sc_filter_attempts a JOIN qc_previews p USING(preview_id) "
            "WHERE a.attempt_id = ? AND a.run_id = ?",
            (parent["attempt_id"], run["run_id"]),
        ).fetchone()
        live_pca = conn.execute(
            "SELECT * FROM sc_pca_attempts WHERE attempt_id = ? AND run_id = ?",
            (pca["attempt_id"], run["run_id"]),
        ).fetchone()
        live_review = conn.execute(
            "SELECT * FROM sc_pca_dimension_reviews WHERE pca_attempt_id = ? ORDER BY rowid DESC LIMIT 1",
            (pca["attempt_id"],),
        ).fetchone()
        return bool(
            live_parent
            and live_pca
            and live_review
            and run["active_preview_id"] == parent["preview_id"]
            and live_parent["approval"] == "approved"
            and live_parent["status"] == live_pca["status"] == "completed"
            and all(
                live_parent[k] == parent[k]
                for k in ("preview_id", "binding_sha256", "tool_version", "directory")
            )
            and json.loads(live_parent["result"]) == parent["result"]
            and all(
                live_pca[k] == pca[k]
                for k in (
                    "filter_attempt_id",
                    "binding_sha256",
                    "tool_version",
                    "protocol",
                    "directory",
                )
            )
            and json.loads(live_pca["result"]) == pca["result"]
            and {**dict(live_review), "dims": json.loads(live_review["dims"])} == review
        )

    def start_sc_cluster(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        snapshot: dict[str, Any],
        binding: str,
        tool_version: str,
        protocol: dict[str, Any],
        reason: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """短事务 CAS 认领，保存完整授权维度和父身份快照。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
            ).fetchone()
            if run is None:
                raise ValueError("QC 关联不存在")
            previous = conn.execute(
                "SELECT * FROM sc_cluster_attempts WHERE pca_attempt_id = ? AND binding_sha256 = ? AND tool_version = ?",
                (snapshot["pca"]["attempt_id"], binding, tool_version),
            ).fetchone()
            if previous:
                if previous["review_id"] != snapshot["review"]["review_id"]:
                    raise ValueError("维度关联冲突")
                return {**dict(previous), "claimed": False}
            self._require_no_filter(conn, run_id)
            if (
                run["revision"] != revision
                or snapshot["review"]["source_revision"] + 1 != revision
                or not self._cluster_source_matches(conn, run, snapshot)
            ):
                raise ValueError("维度或父身份已变化，请刷新后重新授权")
            conn.execute(
                "INSERT INTO sc_cluster_attempts (attempt_id, run_id, pca_attempt_id, review_id, snapshot, "
                "binding_sha256, tool_version, protocol, source_revision, claim_revision, reason, directory, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
                (
                    attempt_id,
                    run_id,
                    snapshot["pca"]["attempt_id"],
                    snapshot["review"]["review_id"],
                    json.dumps(snapshot, ensure_ascii=False),
                    binding,
                    tool_version,
                    json.dumps(protocol),
                    revision,
                    revision + 1,
                    reason,
                    directory,
                    _now(),
                ),
            )
            conn.execute("UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run_id,))
            row = dict(
                conn.execute(
                    "SELECT * FROM sc_cluster_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
        return {**row, "snapshot": snapshot, "claimed": True}

    def finish_sc_cluster(
        self, attempt_id: str, result: dict[str, Any], expected: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """终态/消息/revision 原子保存；外部改变只阻止成功，不阻止失败收口。"""
        if result["status"] not in ("completed", "failed", "interrupted"):
            raise ValueError("聚类终态非法")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute(
                "SELECT * FROM sc_cluster_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if attempt is None or attempt["status"] != "running":
                raise ValueError("聚类任务不在运行状态")
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE run_id = ?", (attempt["run_id"],)
            ).fetchone()
            snapshot = json.loads(attempt["snapshot"])
            if result["status"] == "completed" and (
                expected is None
                or run["revision"] != expected["claim_revision"]
                or snapshot != expected["snapshot"]
                or any(
                    attempt[k] != expected[k]
                    for k in (
                        "run_id",
                        "pca_attempt_id",
                        "review_id",
                        "binding_sha256",
                        "tool_version",
                        "protocol",
                        "source_revision",
                        "claim_revision",
                        "directory",
                        "reason",
                    )
                )
                or not self._cluster_source_matches(conn, run, snapshot)
            ):
                result = {**result, "status": "failed", "error": "运行中授权快照或父身份发生变化"}
            sid, now = run["session_id"], _now()
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?", (sid,)
            ).fetchone()[0]
            message_id = conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, results) VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    sid,
                    seq,
                    "聚类与 UMAP 产物已保存，科学未复核；未执行 markers 或注释。"
                    if result["status"] == "completed"
                    else "聚类与 UMAP 失败或中断，本地证据保留；不会自动重跑。",
                    now,
                    _encode_results(
                        {
                            "sc_cluster": {
                                "run_id": attempt["run_id"],
                                "pca_attempt_id": attempt["pca_attempt_id"],
                                "attempt_id": attempt_id,
                                "stage": "cluster_review",
                                "technical_status": result["status"],
                                "scientific_status": "unreviewed",
                            }
                        }
                    ),
                ),
            ).lastrowid
            conn.execute(
                "UPDATE sc_cluster_attempts SET status = ?, finished_at = ?, result = ?, message_id = ? WHERE attempt_id = ?",
                (
                    result["status"],
                    now,
                    json.dumps(result, ensure_ascii=False),
                    message_id,
                    attempt_id,
                ),
            )
            conn.execute(
                "UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run["run_id"],)
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, sid))
        return result

    def interrupt_sc_clusters(self) -> None:
        """单进程启动中断遗留作业，安全消息与 revision 随终态原子提交。"""
        with self._conn() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT attempt_id FROM sc_cluster_attempts WHERE status = 'running'"
                )
            ]
        for attempt_id in ids:
            self.finish_sc_cluster(
                attempt_id,
                {
                    "status": "interrupted",
                    "stage": "cluster_review",
                    "scientific_status": "unreviewed",
                },
            )

    @classmethod
    def _marker_source_matches(
        cls, conn: sqlite3.Connection, run: sqlite3.Row, snapshot: dict[str, Any]
    ) -> bool:
        """完整当前接受记录与聚类身份必须和认领快照相等。"""
        cluster, review = snapshot["cluster"], snapshot["review"]
        row = conn.execute(
            "SELECT * FROM sc_cluster_reviews WHERE cluster_attempt_id = ? ORDER BY rowid DESC LIMIT 1",
            (cluster["attempt_id"],),
        ).fetchone()
        return bool(
            row
            and {**dict(row), "accepted": bool(row["accepted"])} == review
            and cls._completed_cluster_matches(conn, run, cluster)
        )

    def start_sc_markers(
        self,
        session_id: str,
        run_id: str,
        revision: int,
        snapshot: dict[str, Any],
        binding: str,
        tool_version: str,
        protocol: dict[str, Any],
        reason: str,
        directory: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """短事务 CAS 认领，保存完整聚类接受和父身份快照。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?", (session_id, run_id)
            ).fetchone()
            if run is None:
                raise ValueError("QC 关联不存在")
            previous = conn.execute(
                "SELECT * FROM sc_marker_attempts WHERE cluster_attempt_id = ? AND binding_sha256 = ? AND tool_version = ?",
                (snapshot["cluster"]["attempt_id"], binding, tool_version),
            ).fetchone()
            if previous:
                if previous["review_id"] != snapshot["review"]["review_id"]:
                    raise ValueError("聚类复核关联冲突")
                return {**dict(previous), "claimed": False}
            self._require_no_filter(conn, run_id)
            if (
                run["revision"] != revision
                or snapshot["review"]["source_revision"] + 1 != revision
                or not self._marker_source_matches(conn, run, snapshot)
            ):
                raise ValueError("聚类接受或父身份已变化，请刷新后重新授权")
            conn.execute(
                "INSERT INTO sc_marker_attempts (attempt_id, run_id, cluster_attempt_id, review_id, snapshot, "
                "binding_sha256, tool_version, protocol, source_revision, claim_revision, reason, directory, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
                (
                    attempt_id,
                    run_id,
                    snapshot["cluster"]["attempt_id"],
                    snapshot["review"]["review_id"],
                    json.dumps(snapshot, ensure_ascii=False),
                    binding,
                    tool_version,
                    json.dumps(protocol),
                    revision,
                    revision + 1,
                    reason,
                    directory,
                    _now(),
                ),
            )
            conn.execute("UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run_id,))
            row = dict(
                conn.execute(
                    "SELECT * FROM sc_marker_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
        return {**row, "snapshot": snapshot, "claimed": True}

    def finish_sc_markers(
        self, attempt_id: str, result: dict[str, Any], expected: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """终态/消息/revision 原子保存；外部改变只阻止成功，不阻止失败收口。"""
        if result["status"] not in ("completed", "failed", "interrupted"):
            raise ValueError("marker 终态非法")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute(
                "SELECT * FROM sc_marker_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if attempt is None or attempt["status"] != "running":
                raise ValueError("marker 任务不在运行状态")
            run = conn.execute(
                "SELECT * FROM qc_runs WHERE run_id = ?", (attempt["run_id"],)
            ).fetchone()
            snapshot = json.loads(attempt["snapshot"])
            if result["status"] == "completed" and (
                expected is None
                or run["revision"] != expected["claim_revision"]
                or snapshot != expected["snapshot"]
                or any(
                    attempt[k] != expected[k]
                    for k in (
                        "run_id",
                        "cluster_attempt_id",
                        "review_id",
                        "binding_sha256",
                        "tool_version",
                        "protocol",
                        "source_revision",
                        "claim_revision",
                        "directory",
                        "reason",
                    )
                )
                or not self._marker_source_matches(conn, run, snapshot)
            ):
                result = {**result, "status": "failed", "error": "运行中授权快照或父身份发生变化"}
            sid, now = run["session_id"], _now()
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?", (sid,)
            ).fetchone()[0]
            message_id = conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, results) VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    sid,
                    seq,
                    "marker 候选与诊断产物已保存，科学未复核；未执行细胞类型注释。"
                    if result["status"] == "completed"
                    else "marker 失败或中断，本地证据保留；不会自动重跑。",
                    now,
                    _encode_results(
                        {
                            "sc_markers": {
                                "run_id": attempt["run_id"],
                                "cluster_attempt_id": attempt["cluster_attempt_id"],
                                "attempt_id": attempt_id,
                                "stage": "marker_review",
                                "technical_status": result["status"],
                                "scientific_status": "unreviewed",
                            }
                        }
                    ),
                ),
            ).lastrowid
            conn.execute(
                "UPDATE sc_marker_attempts SET status = ?, finished_at = ?, result = ?, message_id = ? WHERE attempt_id = ?",
                (
                    result["status"],
                    now,
                    json.dumps(result, ensure_ascii=False),
                    message_id,
                    attempt_id,
                ),
            )
            conn.execute(
                "UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run["run_id"],)
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, sid))
        return result

    def interrupt_sc_markers(self) -> None:
        """单进程启动中断遗留作业，安全消息与 revision 随终态原子提交。"""
        with self._conn() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT attempt_id FROM sc_marker_attempts WHERE status = 'running'"
                )
            ]
        for attempt_id in ids:
            self.finish_sc_markers(
                attempt_id,
                {
                    "status": "interrupted",
                    "stage": "marker_review",
                    "scientific_status": "unreviewed",
                },
            )

    @classmethod
    def _completed_cluster_matches(
        cls, conn: sqlite3.Connection, run: sqlite3.Row, cluster: dict[str, Any]
    ) -> bool:
        """短事务重查完整聚类身份及其父快照；文件仍在事务外验证。"""
        row = conn.execute(
            "SELECT * FROM sc_cluster_attempts WHERE attempt_id = ? AND run_id = ?",
            (cluster["attempt_id"], run["run_id"]),
        ).fetchone()
        if row is None or row["status"] != "completed":
            return False
        live = {
            **dict(row),
            "snapshot": json.loads(row["snapshot"]),
            "result": json.loads(row["result"]),
        }
        return live == cluster and cls._cluster_source_matches(conn, run, cluster["snapshot"])

    def record_sc_cluster_review(
        self,
        session_id: str,
        run: dict[str, Any],
        cluster: dict[str, Any],
        binding: str,
        version: str,
        revision: int,
        reason: str,
    ) -> None:
        """接受事实、安全消息与 revision 原子追加；不授权或启动 marker。"""
        # shortcut: 哈希在事务外核实；本地文件不可变隔离留给生产部署片。
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM qc_runs WHERE session_id = ? AND run_id = ?",
                (session_id, run["run_id"]),
            ).fetchone()
            if row is None:
                raise ValueError("QC 关联不存在")
            previous = conn.execute(
                "SELECT * FROM sc_cluster_reviews WHERE cluster_attempt_id = ? "
                "AND binding_sha256 = ? AND record_version = ? AND source_revision = ?",
                (cluster["attempt_id"], binding, version, revision),
            ).fetchone()
            if previous:
                if previous["accepted"] != 1 or previous["reason"] != reason:
                    raise ValueError("同一 revision 的聚类复核内容冲突")
                return
            self._require_no_filter(conn, run["run_id"])
            if row["revision"] != revision or not self._completed_cluster_matches(
                conn, row, cluster
            ):
                raise ValueError("聚类或父身份已变化，请刷新后重新记录")
            latest = conn.execute(
                "SELECT * FROM sc_cluster_reviews WHERE cluster_attempt_id = ? ORDER BY rowid DESC LIMIT 1",
                (cluster["attempt_id"],),
            ).fetchone()
            if (
                latest
                and latest["binding_sha256"] == binding
                and latest["record_version"] == version
                and latest["source_revision"] + 1 == revision
                and latest["reason"] == reason
            ):
                return
            review_id, now = uuid.uuid4().hex, _now()
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            message_id = conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, timestamp, results) "
                "VALUES (?, ?, 'assistant', ?, ?, ?)",
                (
                    session_id,
                    seq,
                    "探索性聚类接受已记录，科学未复核；未授权 marker 或注释。",
                    now,
                    _encode_results(
                        {
                            "sc_cluster_review": {
                                "run_id": run["run_id"],
                                "cluster_attempt_id": cluster["attempt_id"],
                                "review_id": review_id,
                                "stage": "cluster_review",
                                "scientific_status": "unreviewed",
                            }
                        }
                    ),
                ),
            ).lastrowid
            conn.execute(
                "INSERT INTO sc_cluster_reviews VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    review_id,
                    cluster["attempt_id"],
                    binding,
                    version,
                    revision,
                    reason,
                    now,
                    message_id,
                ),
            )
            conn.execute(
                "UPDATE qc_runs SET revision = revision + 1 WHERE run_id = ?", (run["run_id"],)
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def record_knowledge_qa_review(
        self,
        answer_id: str,
        binding_sha256: str,
        record_version: str,
        source_revision: int,
        decision: str,
        reason: str,
        reviewer: str,
        veto: bool = False,
    ) -> dict[str, Any]:
        """追加一条知识问答人工复核事实；同 revision 重发幂等，内容冲突报错。"""
        if decision not in {"accepted", "rejected"}:
            raise ValueError("复核结论必须是 accepted 或 rejected")
        if veto and decision != "rejected":
            raise ValueError("否决项必须标记为 rejected")
        if not reason.strip():
            raise ValueError("复核理由不能为空")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT * FROM knowledge_qa_reviews WHERE answer_id = ? "
                "AND binding_sha256 = ? AND record_version = ? AND source_revision = ?",
                (answer_id, binding_sha256, record_version, source_revision),
            ).fetchone()
            if previous:
                same = (
                    previous["decision"] == decision
                    and bool(previous["veto"]) == veto
                    and previous["reason"] == reason
                    and previous["reviewer"] == reviewer
                )
                if not same:
                    raise ValueError("同一 revision 的知识问答复核内容冲突")
                return dict(previous)
            record = {
                "review_id": uuid.uuid4().hex,
                "answer_id": answer_id,
                "binding_sha256": binding_sha256,
                "record_version": record_version,
                "source_revision": source_revision,
                "decision": decision,
                "veto": int(veto),
                "reason": reason,
                "reviewer": reviewer,
                "created_at": _now(),
            }
            conn.execute(
                "INSERT INTO knowledge_qa_reviews "
                "(review_id, answer_id, binding_sha256, record_version, source_revision, "
                "decision, veto, reason, reviewer, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["review_id"],
                    record["answer_id"],
                    record["binding_sha256"],
                    record["record_version"],
                    record["source_revision"],
                    record["decision"],
                    record["veto"],
                    record["reason"],
                    record["reviewer"],
                    record["created_at"],
                ),
            )
        return record

    def get_knowledge_qa_reviews(self, answer_id: str | None = None) -> list[dict[str, Any]]:
        """按回答读取复核事实；不传 answer_id 时返回全部。"""
        with self._conn() as conn:
            if answer_id is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_qa_reviews ORDER BY created_at, rowid"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM knowledge_qa_reviews WHERE answer_id = ? "
                    "ORDER BY created_at, rowid",
                    (answer_id,),
                ).fetchall()
        return [dict(row) for row in rows]

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


def project_analysis_root(session_id: str | None, repo_root: str | Path) -> Path:
    """新分析产物根：项目会话落 data/projects/<id>/analysis，其余沿用 data/analysis。"""
    base = Path(repo_root) / "data"
    if session_id:
        session = SessionStore().get_session(session_id)
        if session is None:
            raise ValueError("会话不存在，不能确定分析产物根")
        if session.get("project_id"):
            return (base / "projects" / session["project_id"] / "analysis").resolve()
    return (base / "analysis").resolve()


def project_scoped_analysis_root(session_id: str | None, repo_root: str | Path) -> Path | None:
    """仅项目会话返回项目分析根；未归类或无会话返回 None，保留旧输出位置。"""
    if not session_id:
        return None
    session = SessionStore().get_session(session_id)
    if session is None:
        raise ValueError("会话不存在，不能确定分析产物根")
    if not session.get("project_id"):
        return None
    return project_analysis_root(session_id, repo_root)
