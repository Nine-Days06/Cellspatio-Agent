"""公共证据治理：准入、审核状态机、冲突/撤销与检索。

首版是本地假服务器：独立 SQLite + `cards/` 不可变副本，不读取 SessionStore。
角色与 actor 由 CLI 参数声明，只防误操作，不替代正式认证。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.evidence.schema import SCHEMA_VERSION, EvidenceCard, validate_card

SUBMISSION_SCHEMA_VERSION = "cellspatio-evidence-submission/1.0.0"
CONSENT_TEXT_VERSION = "public-evidence-consent/1"
CONSENT_TEXT = "我确认仅上传证据卡的聚合字段，不含原始矩阵、barcode、坐标、样本级 ID 或绝对路径。"

ROLE_CONTRIBUTOR = "contributor"
ROLE_DOMAIN = "domain_reviewer"
ROLE_COMPLIANCE = "compliance_reviewer"

STATE_PENDING = "pending"
STATE_NEEDS_CHANGES = "needs_changes"
STATE_ACCEPTED = "accepted"
STATE_REJECTED = "rejected"
STATE_WITHDRAWN = "withdrawn"

ACTION_SUBMIT = "submit"
ACTION_DOMAIN_PASS = "domain_pass"
ACTION_COMPLIANCE_PASS = "compliance_pass"
ACTION_DOMAIN_NEEDS_CHANGES = "domain_needs_changes"
ACTION_COMPLIANCE_NEEDS_CHANGES = "compliance_needs_changes"
ACTION_DOMAIN_REJECT = "domain_reject"
ACTION_COMPLIANCE_REJECT = "compliance_reject"
ACTION_WITHDRAW = "withdraw"

DOMAIN_CHECKLIST = ("method_complete", "claim_matches_observation", "limitations_present")
COMPLIANCE_CHECKLIST = (
    "privacy_scan_passed",
    "license_and_attribution_present",
    "no_raw_data",
    "small_sample_ok",
)

ACTION_ROLE = {
    ACTION_DOMAIN_PASS: ROLE_DOMAIN,
    ACTION_DOMAIN_NEEDS_CHANGES: ROLE_DOMAIN,
    ACTION_DOMAIN_REJECT: ROLE_DOMAIN,
    ACTION_COMPLIANCE_PASS: ROLE_COMPLIANCE,
    ACTION_COMPLIANCE_NEEDS_CHANGES: ROLE_COMPLIANCE,
    ACTION_COMPLIANCE_REJECT: ROLE_COMPLIANCE,
}
_REVIEW_ACTIONS = tuple(ACTION_ROLE)
_PASS_ACTIONS = (ACTION_DOMAIN_PASS, ACTION_COMPLIANCE_PASS)
_REJECT_ACTIONS = (ACTION_DOMAIN_REJECT, ACTION_COMPLIANCE_REJECT)
_NEEDS_CHANGES_ACTIONS = (ACTION_DOMAIN_NEEDS_CHANGES, ACTION_COMPLIANCE_NEEDS_CHANGES)

MAX_DAILY_SUBMISSIONS = 20
MAX_OPEN_SUBMISSIONS = 20

_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SOURCE_ID_RE = re.compile(
    r"^(?:\d{5,9}|10\.\d{4,9}/\S+|GSE\d+|(?:SRP|SRR|SRS|PRJNA)\d+|E-[A-Z]{4}-\d+)$"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
  submission_id TEXT PRIMARY KEY,
  contributor_id TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  card_id TEXT NOT NULL UNIQUE,
  card_sha256 TEXT NOT NULL,
  card_json TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  gate TEXT NOT NULL,
  supersedes_json TEXT NOT NULL DEFAULT '[]',
  revises_submission_id TEXT,
  donor_count INTEGER,
  independent_replications INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  accepted_at TEXT,
  withdrawn_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  role TEXT NOT NULL,
  action TEXT NOT NULL,
  checklist_json TEXT,
  reason TEXT,
  card_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_submissions_contributor ON submissions(contributor_id);
CREATE INDEX IF NOT EXISTS idx_events_submission ON events(submission_id);
"""


class SubmissionEnvelope(BaseModel):
    """提交信封：治理字段在外，证据卡保持不可变。"""

    schema_version: str = SUBMISSION_SCHEMA_VERSION
    submission_id: str | None = None
    submitted_at: str | None = None
    contributor_id: str
    target_library: Literal["public"] = "public"
    card: EvidenceCard
    supersedes: list[str] = Field(default_factory=list)
    revises_submission_id: str | None = None
    donor_count: int | None = Field(default=None, ge=1)
    independent_replications: int = Field(default=0, ge=0)
    consent: bool = False
    consent_text_version: str = ""


def _now() -> str:
    """当前 UTC 时间（ISO 8601）。"""
    return datetime.now(timezone.utc).isoformat()


def public_evidence_dir(data_root: str | Path) -> Path:
    """公共库固定落在数据根下的 `public_evidence/`。"""
    return Path(data_root).resolve() / "public_evidence"


def _clean(value: Any) -> str:
    """去掉首尾空白后返回字符串。"""
    return str(value or "").strip()


def admission_problems(card: EvidenceCard) -> list[str]:
    """票据 17 的 B 级准入清单；返回全部不满足项。"""
    problems: list[str] = []
    if card.outcome != STATE_ACCEPTED:
        problems.append("只有 outcome=accepted 的证据卡可准入")
    method = card.method
    if not _clean(method.tool):
        problems.append("method.tool 为空")
    if not _clean(method.tool_version):
        problems.append("method.tool_version 为空")
    if not (method.protocol or method.parameters or method.environment):
        problems.append("protocol/parameters/environment 至少一项非空")
    if not _clean(card.claim.text):
        problems.append("claim.text 为空")
    if not _clean(card.license.spdx):
        problems.append("license.spdx 为空")
    if not _clean(card.license.attribution):
        problems.append("license.attribution 为空")
    if card.gate == "knowledge_qa_review" and not card.provenance.source_ids:
        problems.append("问答卡必须至少有一个来源 ID")
    for source_id in card.provenance.source_ids:
        if not _SOURCE_ID_RE.match(source_id):
            problems.append(f"来源 ID 格式不支持：{source_id}")
    return problems


def _require_checklist(checklist: dict[str, Any], required: tuple[str, ...]) -> None:
    """清单项必须全部为 true；缺项或 false 都拒绝。"""
    failed = [name for name in required if checklist.get(name) is not True]
    if failed:
        raise ValueError("复核清单未全部通过：" + "、".join(failed))


class GovernanceStore:
    """公共证据假服务器：提交、审核、撤销、检索与审计事件。"""

    def __init__(
        self,
        root: str | Path,
        *,
        max_daily_submissions: int = MAX_DAILY_SUBMISSIONS,
        max_open_submissions: int = MAX_OPEN_SUBMISSIONS,
    ):
        """绑定独立目录；数据库为 `<root>/governance.db`，卡副本为 `<root>/cards/`。"""
        self.root = Path(root)
        self.db_path = self.root / "governance.db"
        self.cards_dir = self.root / "cards"
        self.max_daily_submissions = max_daily_submissions
        self.max_open_submissions = max_open_submissions

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """打开短连接并确保 schema 存在。"""
        self.root.mkdir(parents=True, exist_ok=True)
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

    def _row(self, conn: sqlite3.Connection, submission_id: str) -> sqlite3.Row:
        """按 submission_id 取提交，缺失即拒绝。"""
        row = conn.execute(
            "SELECT * FROM submissions WHERE submission_id=?", (submission_id,)
        ).fetchone()
        if row is None:
            raise ValueError("提交不存在")
        return row

    def _event(
        self,
        conn: sqlite3.Connection,
        submission_id: str,
        actor_id: str,
        role: str,
        action: str,
        checklist: dict[str, Any] | None,
        reason: str | None,
        card_sha256: str,
        created_at: str,
    ) -> None:
        """追加一条不可变审计事件。"""
        conn.execute(
            "INSERT INTO events "
            "(submission_id, actor_id, role, action, checklist_json, reason, card_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                submission_id,
                actor_id,
                role,
                action,
                json.dumps(checklist, ensure_ascii=False) if checklist is not None else None,
                reason,
                card_sha256,
                created_at,
            ),
        )

    def _write_card(self, card: EvidenceCard, card_json: str, card_sha256: str) -> None:
        """卡副本只写一次；内容不一致说明 card_id 被复用。"""
        if not _ID_RE.match(card.card_id):
            raise ValueError("card_id 只能包含字母、数字、点、下划线和短横线")
        path = self.cards_dir / f"{card.card_id}.json"
        if path.exists():
            existing = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if existing != card_sha256:
                raise ValueError("同一 card_id 的卡内容不一致")
            return
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(card_json, encoding="utf-8")

    def _check_supersedes(
        self, conn: sqlite3.Connection, submission_id: str, supersedes: list[str]
    ) -> None:
        """supersedes 只能指向已接受的既有提交，且整张引用图必须无环。"""
        for target in supersedes:
            if target == submission_id:
                raise ValueError("supersedes 不能指向自己")
            row = conn.execute(
                "SELECT status FROM submissions WHERE submission_id=?", (target,)
            ).fetchone()
            if row is None:
                raise ValueError(f"supersedes 指向不存在的提交：{target}")
            if row["status"] != STATE_ACCEPTED:
                raise ValueError(f"只能取代 accepted 证据：{target}")
        edges = {
            row["submission_id"]: json.loads(row["supersedes_json"])
            for row in conn.execute("SELECT submission_id, supersedes_json FROM submissions")
        }
        edges[submission_id] = list(supersedes)
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            # shortcut: 递归 DFS，supersedes 链超过 1000 层时需改迭代
            if state.get(node) == 1:
                raise ValueError("supersedes 引用存在环")
            if state.get(node) == 2:
                return
            state[node] = 1
            for target in edges.get(node, []):
                visit(target)
            state[node] = 2

        for node in edges:
            visit(node)

    def _check_limits(self, conn: sqlite3.Connection, contributor_id: str, now: str) -> None:
        """按 actor 限制每日提交数与未终态（含 accepted）提交数。"""
        day_start = datetime.fromisoformat(now).replace(hour=0, minute=0, second=0, microsecond=0)
        daily = conn.execute(
            "SELECT COUNT(*) FROM events WHERE actor_id=? AND action=? AND created_at>=?",
            (contributor_id, ACTION_SUBMIT, day_start.isoformat()),
        ).fetchone()[0]
        if daily >= self.max_daily_submissions:
            raise ValueError("超过每日提交限额")
        open_count = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE contributor_id=? AND status NOT IN (?, ?)",
            (contributor_id, STATE_REJECTED, STATE_WITHDRAWN),
        ).fetchone()[0]
        if open_count >= self.max_open_submissions:
            raise ValueError("未终态提交数超过限额")

    def submit(self, envelope: SubmissionEnvelope | dict[str, Any]) -> dict[str, Any]:
        """校验并接收一个提交，初始状态一律为 pending。"""
        env = (
            envelope
            if isinstance(envelope, SubmissionEnvelope)
            else SubmissionEnvelope.model_validate(envelope)
        )
        if env.schema_version != SUBMISSION_SCHEMA_VERSION:
            raise ValueError(f"提交 schema_version 必须是 {SUBMISSION_SCHEMA_VERSION}")
        if not env.consent:
            raise ValueError("贡献者必须确认 consent")
        if env.consent_text_version != CONSENT_TEXT_VERSION:
            raise ValueError(f"consent_text_version 必须是 {CONSENT_TEXT_VERSION}")
        if not _clean(env.contributor_id):
            raise ValueError("contributor_id 必填")
        try:
            validate_card(env.card)
        except ValueError as exc:
            raise ValueError(f"证据卡校验失败：{exc}") from exc
        problems = admission_problems(env.card)
        if problems:
            raise ValueError("准入失败：" + "；".join(problems))
        if env.card.schema_version != SCHEMA_VERSION:
            raise ValueError(f"证据卡 schema_version 必须是 {SCHEMA_VERSION}")

        submission_id = env.submission_id or f"evs-{uuid.uuid4().hex}"
        if not _ID_RE.match(submission_id):
            raise ValueError("submission_id 只能包含字母、数字、点、下划线和短横线")
        card_json = env.card.model_dump_json()
        card_sha256 = hashlib.sha256(card_json.encode("utf-8")).hexdigest()
        now = _now()
        if env.submitted_at:
            try:
                datetime.fromisoformat(env.submitted_at)
            except ValueError as exc:
                raise ValueError("submitted_at 必须是 ISO 8601 时间") from exc
        submitted_at = env.submitted_at or now

        with self._conn() as conn:
            if conn.execute(
                "SELECT 1 FROM submissions WHERE card_id=?", (env.card.card_id,)
            ).fetchone():
                raise ValueError("同一 card_id 只能提交一次")
            if conn.execute(
                "SELECT 1 FROM submissions WHERE submission_id=?", (submission_id,)
            ).fetchone():
                raise ValueError("submission_id 已存在")
            self._check_supersedes(conn, submission_id, env.supersedes)
            if env.revises_submission_id:
                original = self._row(conn, env.revises_submission_id)
                if original["contributor_id"] != env.contributor_id:
                    raise ValueError("只能修订自己的提交")
                if original["status"] != STATE_NEEDS_CHANGES:
                    raise ValueError("只能修订 needs_changes 的提交")
            self._check_limits(conn, env.contributor_id, now)
            self._write_card(env.card, card_json, card_sha256)
            conn.execute(
                "INSERT INTO submissions "
                "(submission_id, contributor_id, submitted_at, card_id, card_sha256, card_json, "
                "claim_text, gate, supersedes_json, revises_submission_id, donor_count, "
                "independent_replications, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    env.contributor_id,
                    submitted_at,
                    env.card.card_id,
                    card_sha256,
                    card_json,
                    env.card.claim.text,
                    env.card.gate,
                    json.dumps(env.supersedes, ensure_ascii=False),
                    env.revises_submission_id,
                    env.donor_count,
                    env.independent_replications,
                    STATE_PENDING,
                ),
            )
            self._event(
                conn,
                submission_id,
                env.contributor_id,
                ROLE_CONTRIBUTOR,
                ACTION_SUBMIT,
                None,
                None,
                card_sha256,
                now,
            )
        return self.summary(submission_id)

    def _check_distinct_signer(
        self, conn: sqlite3.Connection, submission_id: str, actor_id: str, action: str
    ) -> None:
        """领域与合规签署人必须是不同 actor。"""
        other = ACTION_COMPLIANCE_PASS if action == ACTION_DOMAIN_PASS else ACTION_DOMAIN_PASS
        row = conn.execute(
            "SELECT 1 FROM events WHERE submission_id=? AND action=? AND actor_id=?",
            (submission_id, other, actor_id),
        ).fetchone()
        if row is not None:
            raise ValueError("同一 actor 不能同时签署领域与合规")

    def _next_state(self, conn: sqlite3.Connection, submission_id: str, action: str) -> str:
        """根据已记录事件推导新状态。"""
        if action in _REJECT_ACTIONS:
            return STATE_REJECTED
        if action in _NEEDS_CHANGES_ACTIONS:
            return STATE_NEEDS_CHANGES
        passes = {
            row["action"]
            for row in conn.execute(
                "SELECT action FROM events WHERE submission_id=? AND action IN (?, ?)",
                (submission_id, *_PASS_ACTIONS),
            )
        }
        if set(_PASS_ACTIONS) <= passes:
            return STATE_ACCEPTED
        return STATE_PENDING

    def review(
        self,
        submission_id: str,
        *,
        actor_id: str,
        role: str,
        action: str,
        checklist: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """记录一次复核；两个 pass 到齐时自动转 accepted。"""
        if not _clean(actor_id):
            raise ValueError("actor_id 必填")
        if action not in _REVIEW_ACTIONS:
            raise ValueError("未知审核动作")
        if ACTION_ROLE[action] != role:
            raise ValueError(f"动作 {action} 需要角色 {ACTION_ROLE[action]}")
        reason = _clean(reason)
        now = _now()
        with self._conn() as conn:
            row = self._row(conn, submission_id)
            if row["status"] != STATE_PENDING:
                raise ValueError("只有 pending 提交可审核")
            if actor_id == row["contributor_id"]:
                raise ValueError("贡献者不得审核自己的提交")
            if action in _PASS_ACTIONS:
                required = DOMAIN_CHECKLIST if role == ROLE_DOMAIN else COMPLIANCE_CHECKLIST
                _require_checklist(checklist or {}, required)
                self._check_distinct_signer(conn, submission_id, actor_id, action)
                if conn.execute(
                    "SELECT 1 FROM events WHERE submission_id=? AND action=?",
                    (submission_id, action),
                ).fetchone():
                    raise ValueError("该角色已经通过")
            elif not reason:
                raise ValueError("needs_changes/rejected 必须填写原因")
            self._event(
                conn,
                submission_id,
                actor_id,
                role,
                action,
                checklist,
                reason or None,
                row["card_sha256"],
                now,
            )
            state = self._next_state(conn, submission_id, action)
            conn.execute(
                "UPDATE submissions SET status=?, accepted_at=? WHERE submission_id=?",
                (state, now if state == STATE_ACCEPTED else None, submission_id),
            )
        return self.summary(submission_id)

    def withdraw(
        self,
        submission_id: str,
        *,
        actor_id: str,
        role: str,
        reason: str,
    ) -> dict[str, Any]:
        """贡献者撤回自己的 accepted 提交，或合规复核者强制撤销。"""
        reason = _clean(reason)
        if not _clean(actor_id):
            raise ValueError("actor_id 必填")
        if not reason:
            raise ValueError("撤销必须填写原因")
        now = _now()
        with self._conn() as conn:
            row = self._row(conn, submission_id)
            if row["status"] != STATE_ACCEPTED:
                raise ValueError("只有 accepted 提交可撤销")
            if role == ROLE_CONTRIBUTOR:
                if actor_id != row["contributor_id"]:
                    raise ValueError("只能撤回自己的提交")
            elif role != ROLE_COMPLIANCE:
                raise ValueError("只有贡献者或合规复核者可撤销")
            conn.execute(
                "UPDATE submissions SET status=?, withdrawn_at=? WHERE submission_id=?",
                (STATE_WITHDRAWN, now, submission_id),
            )
            self._event(
                conn,
                submission_id,
                actor_id,
                role,
                ACTION_WITHDRAW,
                None,
                reason,
                row["card_sha256"],
                now,
            )
        return self.summary(submission_id)

    def _superseded_map(self, conn: sqlite3.Connection) -> dict[str, str]:
        """目标 -> 最新的 accepted 后继；后继撤销后自动消失。"""
        mapping: dict[str, str] = {}
        rows = conn.execute(
            "SELECT submission_id, supersedes_json FROM submissions "
            "WHERE status=? ORDER BY accepted_at, submission_id",
            (STATE_ACCEPTED,),
        )
        for row in rows:
            for target in json.loads(row["supersedes_json"]):
                mapping[target] = row["submission_id"]
        return mapping

    def _summary(self, row: sqlite3.Row, superseded: dict[str, str]) -> dict[str, Any]:
        """检索结果与 CLI 输出共用的提交摘要。"""
        return {
            "evidence_id": row["submission_id"],
            "card_id": row["card_id"],
            "gate": row["gate"],
            "status": row["status"],
            "claim": row["claim_text"] if row["status"] == STATE_ACCEPTED else None,
            "contributor_id": row["contributor_id"],
            "submitted_at": row["submitted_at"],
            "donor_count": row["donor_count"],
            "independent_replications": row["independent_replications"],
            "supersedes": json.loads(row["supersedes_json"]),
            "superseded_by": superseded.get(row["submission_id"]),
            "accepted_at": row["accepted_at"],
            "withdrawn_at": row["withdrawn_at"],
        }

    def summary(self, submission_id: str) -> dict[str, Any]:
        """读取单个提交摘要。"""
        with self._conn() as conn:
            return self._summary(self._row(conn, submission_id), self._superseded_map(conn))

    def submissions_by_contributor(self, contributor_id: str) -> list[dict[str, Any]]:
        """本人的全部提交摘要，附带最近一次带原因的动作。"""
        if not _clean(contributor_id):
            raise ValueError("contributor_id 必填")
        with self._conn() as conn:
            superseded = self._superseded_map(conn)
            rows = conn.execute(
                "SELECT * FROM submissions WHERE contributor_id=? "
                "ORDER BY submitted_at DESC, submission_id DESC",
                (contributor_id,),
            ).fetchall()
            items = []
            for row in rows:
                item = self._summary(row, superseded)
                item["claim"] = row["claim_text"]
                latest = conn.execute(
                    "SELECT action, reason FROM events WHERE submission_id=? "
                    "AND reason IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (row["submission_id"],),
                ).fetchone()
                item["latest_reason"] = latest["reason"] if latest else None
                items.append(item)
            return items

    def search(self, text: str = "", gate: str | None = None) -> list[dict[str, Any]]:
        """只检索 accepted 提交；被取代项保留但降权。"""
        with self._conn() as conn:
            superseded = self._superseded_map(conn)
            rows = conn.execute(
                "SELECT * FROM submissions WHERE status=?", (STATE_ACCEPTED,)
            ).fetchall()
            results = [
                self._summary(row, superseded)
                for row in rows
                if (not gate or row["gate"] == gate)
                and (not text or text.lower() in row["claim_text"].lower())
            ]
        results.sort(key=lambda item: item["evidence_id"])
        results.sort(key=lambda item: item["accepted_at"] or "", reverse=True)
        results.sort(key=lambda item: item["donor_count"] or 0, reverse=True)
        results.sort(key=lambda item: item["independent_replications"], reverse=True)
        results.sort(key=lambda item: item["superseded_by"] is not None)
        return results

    def get(
        self,
        submission_id: str,
        *,
        include_unaccepted: bool = False,
        viewer_actor: str | None = None,
        viewer_role: str | None = None,
    ) -> dict[str, Any]:
        """accepted 返回全文；其它状态默认只返回墓碑。"""
        with self._conn() as conn:
            row = self._row(conn, submission_id)
            if include_unaccepted and row["status"] != STATE_ACCEPTED:
                if viewer_role not in (ROLE_DOMAIN, ROLE_COMPLIANCE):
                    raise ValueError("只有复核者可读取未发布全文")
                if not _clean(viewer_actor):
                    raise ValueError("复核者视角需要 actor_id")
                if viewer_actor == row["contributor_id"]:
                    raise ValueError("贡献者不得读取自己的未发布全文")
            if row["status"] == STATE_ACCEPTED or (
                include_unaccepted
                and row["status"] in (STATE_PENDING, STATE_NEEDS_CHANGES, STATE_REJECTED)
            ):
                return {
                    **self._summary(row, self._superseded_map(conn)),
                    "card": json.loads(row["card_json"]),
                }
            last = conn.execute(
                "SELECT action, actor_id, reason, created_at FROM events "
                "WHERE submission_id=? ORDER BY id DESC LIMIT 1",
                (submission_id,),
            ).fetchone()
        return {
            "evidence_id": row["submission_id"],
            "card_id": row["card_id"],
            "gate": row["gate"],
            "status": row["status"],
            "action": last["action"],
            "actor_id": last["actor_id"],
            "reason": last["reason"],
            "at": last["created_at"],
        }

    def events(self, submission_id: str) -> list[dict[str, Any]]:
        """按时间顺序返回审计事件。"""
        with self._conn() as conn:
            self._row(conn, submission_id)
            rows = conn.execute(
                "SELECT actor_id, role, action, checklist_json, reason, card_sha256, created_at "
                "FROM events WHERE submission_id=? ORDER BY id",
                (submission_id,),
            ).fetchall()
        return [self._event_record(row) for row in rows]

    @staticmethod
    def _event_record(row: sqlite3.Row, *, with_submission: bool = False) -> dict[str, Any]:
        """事件行 → 审计字典；可按需带上提交定位。"""
        record = {
            "actor_id": row["actor_id"],
            "role": row["role"],
            "action": row["action"],
            "checklist": json.loads(row["checklist_json"]) if row["checklist_json"] else None,
            "reason": row["reason"],
            "card_sha256": row["card_sha256"],
            "created_at": row["created_at"],
        }
        if with_submission:
            return {
                "event_id": row["id"],
                "submission_id": row["submission_id"],
                **record,
            }
        return record

    def audit_records(self) -> Iterator[dict[str, Any]]:
        """导出全部提交与事件，供离线审计；不读取账号库。"""
        with self._conn() as conn:
            superseded = self._superseded_map(conn)
            submissions = conn.execute(
                "SELECT * FROM submissions ORDER BY submitted_at, submission_id"
            ).fetchall()
            for row in submissions:
                yield {
                    "type": "submission",
                    **self._summary(row, superseded),
                    "card": json.loads(row["card_json"]),
                }
            events = conn.execute(
                "SELECT id, submission_id, actor_id, role, action, checklist_json, reason, "
                "card_sha256, created_at FROM events ORDER BY id"
            ).fetchall()
            for row in events:
                yield {"type": "event", **self._event_record(row, with_submission=True)}

    def review_queue(self, *, viewer_actor: str, viewer_role: str) -> list[dict[str, Any]]:
        """复核队列：返回待审且本角色尚未签署、本人也未审过的全文。"""
        if viewer_role not in (ROLE_DOMAIN, ROLE_COMPLIANCE):
            raise ValueError("只有复核者可查看审核队列")
        if not _clean(viewer_actor):
            raise ValueError("审核队列需要 actor_id")
        pass_action = ACTION_DOMAIN_PASS if viewer_role == ROLE_DOMAIN else ACTION_COMPLIANCE_PASS
        with self._conn() as conn:
            superseded = self._superseded_map(conn)
            rows = conn.execute(
                "SELECT * FROM submissions s WHERE status=? AND contributor_id<>? "
                "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.submission_id=s.submission_id "
                "AND (e.actor_id=? OR e.action=?)) ORDER BY submitted_at, submission_id",
                (STATE_PENDING, viewer_actor, viewer_actor, pass_action),
            ).fetchall()
            return [
                {**self._summary(row, superseded), "card": json.loads(row["card_json"])}
                for row in rows
            ]
