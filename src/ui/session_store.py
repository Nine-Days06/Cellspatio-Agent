"""SQLite 会话存储：单库短连接 + WAL，逐次调用独立连接。"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.ui.confirm_protocol import STATUS_CONFIRMED, STATUS_EXPIRED, STATUS_PENDING
from src.ui.run_snapshot import RunSnapshot
from src.ui.sc_stage_defs import STAGE_SPECS
from src.ui.sc_stages import ScStages
from src.ui.session_codec import _encode_results, _now
from src.ui.stage_lifecycle import ReviewFactPlan, advance_run, append_review_fact, publish_message
from src.ui.store_errors import StoreError

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(".cellspatio") / "sessions.db"
SCHEMA_VERSION = 1

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
    rejected_at TEXT,
    reviewer TEXT,
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
CREATE TABLE IF NOT EXISTS sc_cluster_rejections (
    review_id TEXT PRIMARY KEY,
    cluster_attempt_id TEXT NOT NULL REFERENCES sc_cluster_attempts(attempt_id) ON DELETE CASCADE,
    binding_sha256 TEXT NOT NULL,
    record_version TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(cluster_attempt_id, binding_sha256, record_version, source_revision)
);
CREATE TABLE IF NOT EXISTS sc_marker_reviews (
    review_id TEXT PRIMARY KEY,
    marker_attempt_id TEXT NOT NULL REFERENCES sc_marker_attempts(attempt_id) ON DELETE CASCADE,
    binding_sha256 TEXT NOT NULL,
    record_version TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
    reason TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(marker_attempt_id, binding_sha256, record_version, source_revision)
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
    preview_columns = {row[1] for row in conn.execute("PRAGMA table_info(qc_previews)").fetchall()}
    for name, ddl in {"rejected_at": "TEXT", "reviewer": "TEXT"}.items():
        if name not in preview_columns:
            conn.execute(f"ALTER TABLE qc_previews ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at)")


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


def _running_stage(conn: sqlite3.Connection, session_id: str) -> str | None:
    """在给定连接（含写事务）内查正在运行的受控阶段。"""
    for spec in STAGE_SPECS:
        sql = (
            f"SELECT 1 FROM {spec.table} a JOIN qc_runs r USING(run_id) "
            "WHERE r.session_id=? AND a.status='running'"
        )
        if conn.execute(sql, (session_id,)).fetchone():
            return spec.label
    return None


class SessionStore:
    """会话/消息 CRUD。每次操作独立短连接，不做连接池。"""

    def __init__(self, db_path: str | Path | None = None):
        path = db_path or os.environ.get("CELLSPATIO_SESSIONS_DB") or DEFAULT_DB_PATH
        self.db_path = Path(path)

    @property
    def stages(self) -> ScStages:
        """受控阶段操作的窄接口。"""
        return ScStages(self)

    @contextmanager
    def _conn(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # shortcut: 新增/修改表结构时必须同时递增 SCHEMA_VERSION，否则旧库不会迁移
            if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                conn.executescript(SCHEMA)
                _migrate_schema(conn)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_session(self, title: str = "新会话", project_id: str | None = None) -> str:
        sid = uuid.uuid4().hex
        now = _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if project_id is not None:
                # 校验必须在写事务内：归档可与建会话并发。
                project = conn.execute(
                    "SELECT archived_at FROM projects WHERE id=?", (project_id,)
                ).fetchone()
                if project is None:
                    raise StoreError("not_found", "项目不存在")
                if project["archived_at"]:
                    raise StoreError("conflict", "项目已归档")
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
            if archived:
                # 与归档写入同一写事务，避免预检后会话被并发领取阶段
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT id FROM sessions WHERE project_id=? AND archived_at IS NULL",
                    (project_id,),
                ).fetchall()
                for row in rows:
                    running = _running_stage(conn, row["id"])
                    if running:
                        raise StoreError("conflict", f"{running}运行中，不能归档项目")
            cursor = conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", vals)
            if cursor.rowcount == 0:
                raise StoreError("not_found", "项目不存在")

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
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if project_id is not None:
                project = conn.execute(
                    "SELECT archived_at FROM projects WHERE id=?", (project_id,)
                ).fetchone()
                if project is None:
                    raise StoreError("not_found", "项目不存在")
                if project["archived_at"]:
                    raise StoreError("conflict", "项目已归档")
            previous = conn.execute(
                "SELECT project_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            cursor = conn.execute(
                "UPDATE sessions SET project_id=?, updated_at=? WHERE id=?",
                (project_id, _now(), session_id),
            )
            if cursor.rowcount == 0:
                raise StoreError("not_found", "会话不存在")
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
                raise StoreError("not_found", "会话不存在")

    def archive_session(self, session_id: str, archived: bool = True) -> None:
        """归档或恢复会话；运行中的受控阶段拒绝归档。"""
        with self._conn() as conn:
            if archived:
                # 预检与写入同一写事务，避免检查后被并发领取阶段
                conn.execute("BEGIN IMMEDIATE")
                running = _running_stage(conn, session_id)
                if running:
                    raise StoreError("conflict", f"{running}运行中，不能归档会话")
            cursor = conn.execute(
                "UPDATE sessions SET archived_at=?, updated_at=? WHERE id=?",
                (_now() if archived else None, _now(), session_id),
            )
            if cursor.rowcount == 0:
                raise StoreError("not_found", "会话不存在")

    def delete_session(self, session_id: str) -> None:
        """删除会话及其消息；运行中的受控阶段拒绝删除。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            running = _running_stage(conn, session_id)
            if running:
                raise StoreError("conflict", f"{running}运行中，不能删除会话")
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def running_stage(self, session_id: str) -> str | None:
        """返回正在运行的受控阶段名；供删除/归档前预检。"""
        with self._conn() as conn:
            return _running_stage(conn, session_id)

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        results: dict[str, Any] | None = None,
        pending_script: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """追加消息并返回落库行（含 id、seq、解码后的 results）。"""
        now = _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = row["next_seq"]
            cursor = conn.execute(
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
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        message = dict(row)
        message["results"] = _decode_results(message["results"])
        message["pending_script"] = (
            json.loads(message["pending_script"]) if message["pending_script"] else None
        )
        return message

    def register_qc_run(
        self, session_id: str, run_id: str, directory: str, evidence: dict[str, Any]
    ) -> None:
        """认领受控层校验过的 QC；唯一约束禁止跨会话补认领。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO qc_runs (run_id, session_id, directory, evidence) VALUES (?, ?, ?, ?)",
                (run_id, session_id, directory, json.dumps(evidence, ensure_ascii=False)),
            )

    def get_qc_run(self, session_id: str, run_id: str) -> RunSnapshot | None:
        """读取权威 run 与预览/批准历史，不从消息倒推状态。"""
        with self._conn() as conn:
            return RunSnapshot.load(conn, session_id, run_id)

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
                (
                    preview["preview_id"],
                    run_id,
                    json.dumps({**preview, "source_revision": revision}, ensure_ascii=False),
                ),
            )

    def approve_qc_preview(
        self, session_id: str, run_id: str, revision: int, preview_id: str, reason: str
    ) -> RunSnapshot | None:
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
            if preview["status"] == "rejected":
                raise ValueError("该预览已被拒绝，请重新预览")
            if preview["status"] != "approved":
                if run["revision"] != revision or run["active_preview_id"] != preview_id:
                    raise ValueError("预览已失效，请刷新后重新预览")
                payload = json.loads(preview["payload"])
                if payload["summary"]["blocked"]:
                    raise ValueError("该预览不能批准")
                now = _now()
                message_id = publish_message(
                    conn,
                    session_id,
                    "过滤选择已记录，尚未执行过滤或下游分析。",
                    {"qc_selection": {"run_id": run_id, "preview_id": preview_id}},
                    now,
                    _encode_results,
                )
                conn.execute(
                    "UPDATE qc_previews SET status = 'approved', approved_at = ?, reason = ?, message_id = ? "
                    "WHERE preview_id = ? AND status = 'pending'",
                    (now, reason, message_id, preview_id),
                )
                advance_run(conn, run_id, session_id, now)
        return self.get_qc_run(session_id, run_id)

    def record_qc_rejection(
        self,
        session_id: str,
        run_id: str,
        preview_id: str,
        reason: str,
        reviewer: str = "local-user",
    ) -> RunSnapshot | None:
        """拒绝当前待批准的 QC 预览；只记事实，不执行过滤。"""
        reason = reason.strip()
        if not reason:
            raise ValueError("拒绝必须填写原因")
        reviewer = reviewer.strip() or "local-user"
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
            if preview["status"] == "rejected":
                if preview["reason"] != reason or preview["reviewer"] != reviewer:
                    raise ValueError("同一预览的拒绝内容冲突")
            else:
                if preview["status"] != "pending" or run["active_preview_id"] != preview_id:
                    raise ValueError("仅可拒绝当前待批准预览")
                now = _now()
                message_id = publish_message(
                    conn,
                    session_id,
                    "QC 阈值预览已拒绝；未执行过滤或下游分析。",
                    {"qc_rejection": {"run_id": run_id, "preview_id": preview_id}},
                    now,
                    _encode_results,
                )
                conn.execute(
                    "UPDATE qc_previews SET status = 'rejected', rejected_at = ?, reviewer = ?, "
                    "reason = ?, message_id = ? WHERE preview_id = ? AND status = 'pending'",
                    (now, reviewer, reason, message_id, preview_id),
                )
                advance_run(conn, run_id, session_id, now)
        return self.get_qc_run(session_id, run_id)

    @staticmethod
    def _require_no_filter(conn: sqlite3.Connection, run_id: str) -> None:
        """运行中的选择不能被新预览或批准替换。"""
        for spec in STAGE_SPECS:
            if conn.execute(
                f"SELECT 1 FROM {spec.table} WHERE run_id = ? AND status = 'running'", (run_id,)
            ).fetchone():
                raise ValueError(f"{spec.label}运行中，不能修改选择")

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

        def same_previous(row: sqlite3.Row) -> bool:
            """同 revision 内容一致才幂等。"""
            return (
                row["decision"] == decision
                and bool(row["veto"]) == veto
                and row["reason"] == reason
                and row["reviewer"] == reviewer
            )

        return append_review_fact(
            self._conn,
            _encode_results,
            ReviewFactPlan(
                table="knowledge_qa_reviews",
                previous_match={
                    "answer_id": answer_id,
                    "binding_sha256": binding_sha256,
                    "record_version": record_version,
                    "source_revision": source_revision,
                },
                insert_values={
                    "answer_id": answer_id,
                    "binding_sha256": binding_sha256,
                    "record_version": record_version,
                    "source_revision": source_revision,
                    "decision": decision,
                    "veto": int(veto),
                    "reason": reason,
                    "reviewer": reviewer,
                },
                same_previous=same_previous,
                conflict_message="同一 revision 的知识问答复核内容冲突",
            ),
            clock=_now,
        ).fact

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

    def append_downloaded_asset(
        self, session_id: str, asset: dict[str, Any], *, source: str
    ) -> None:
        """在写事务内按 (source, asset_id) 去重追加，避免读-改-写丢资产。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT downloaded_assets FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                return
            assets = _decode_downloaded(row["downloaded_assets"])
            key = (asset.get("source") or source, asset.get("asset_id"))
            if key in {(a.get("source") or source, a.get("asset_id")) for a in assets}:
                return
            assets.append(asset)
            conn.execute(
                "UPDATE sessions SET downloaded_assets=?, updated_at=? WHERE id=?",
                (json.dumps(assets, ensure_ascii=False), _now(), session_id),
            )

    def replace_local_asset(self, session_id: str, asset: dict[str, Any]) -> None:
        """写事务内把 source=local 的资产替换为给定项（同一会话只留最新本地输入）。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT downloaded_assets FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                return
            assets = [
                a
                for a in _decode_downloaded(row["downloaded_assets"])
                if a.get("source") != "local"
            ]
            assets.append(asset)
            conn.execute(
                "UPDATE sessions SET downloaded_assets=?, updated_at=? WHERE id=?",
                (json.dumps(assets, ensure_ascii=False), _now(), session_id),
            )

    def set_script_status(
        self, session_id: str, status: str, *, message_id: int | None = None
    ) -> None:
        """更新指定（缺省最新）一条 pending/expired 脚本消息的状态。"""
        with self._conn() as conn:
            # 与 claim_script 共用写锁：避免 restore 把刚 confirmed 的卡片改回 expired。
            conn.execute("BEGIN IMMEDIATE")
            if message_id is None:
                row = conn.execute(
                    "SELECT id, pending_script FROM messages "
                    "WHERE session_id = ? AND pending_script IS NOT NULL "
                    "ORDER BY seq DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, pending_script FROM messages "
                    "WHERE session_id = ? AND id = ? AND pending_script IS NOT NULL",
                    (session_id, message_id),
                ).fetchone()
            if row is None:
                return
            script = json.loads(row["pending_script"])
            if script.get("status") not in (STATUS_PENDING, STATUS_EXPIRED):
                return
            script["status"] = status
            conn.execute(
                "UPDATE messages SET pending_script = ? WHERE id = ?",
                (json.dumps(script, ensure_ascii=False), row["id"]),
            )

    def claim_script(
        self, session_id: str, status: str, *, message_id: int | None = None
    ) -> tuple[int, dict[str, Any]] | None:
        """把指定（缺省最新）pending 脚本原子改成 status，返回 (消息 id, 脚本)。

        返回的脚本带 claim_token；执行失败时 release_script 用它做 CAS，
        避免把别人已推进的终态覆盖回 pending。
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if message_id is None:
                row = conn.execute(
                    "SELECT id, pending_script FROM messages "
                    "WHERE session_id = ? AND pending_script IS NOT NULL "
                    "ORDER BY seq DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, pending_script FROM messages "
                    "WHERE session_id = ? AND id = ? AND pending_script IS NOT NULL",
                    (session_id, message_id),
                ).fetchone()
            if row is None:
                return None
            script = json.loads(row["pending_script"])
            if script.get("status") != STATUS_PENDING:
                return None
            script["status"] = status
            # 只有「确认执行」需要回滚令牌；取消是终态，不需要。
            if status == STATUS_CONFIRMED:
                script["claim_token"] = uuid.uuid4().hex
            conn.execute(
                "UPDATE messages SET pending_script = ? WHERE id = ?",
                (json.dumps(script, ensure_ascii=False), row["id"]),
            )
            return int(row["id"]), script

    def release_script(self, session_id: str, message_id: int, token: str) -> None:
        """把本次领取的脚本卡退回 pending；已被他人推进则静默放弃。"""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT pending_script FROM messages WHERE session_id = ? AND id = ?",
                (session_id, message_id),
            ).fetchone()
            if row is None or not row["pending_script"]:
                return
            script = json.loads(row["pending_script"])
            if script.get("status") != STATUS_CONFIRMED:
                return
            if script.get("claim_token") != token:
                return
            script["status"] = STATUS_PENDING
            script.pop("claim_token", None)
            conn.execute(
                "UPDATE messages SET pending_script = ? WHERE id = ?",
                (json.dumps(script, ensure_ascii=False), message_id),
            )


def _decode_downloaded(text: str | None) -> list:
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:  # 容错：坏数据按空列表处理
        logger.warning("downloaded_assets JSON 解析失败，按空列表处理")
        return []
