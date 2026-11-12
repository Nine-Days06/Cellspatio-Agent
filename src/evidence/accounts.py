"""公共证据服务的账号与角色：独立 SQLite，不复用本地会话库。

密码用 Argon2，登录签发 Bearer token（库内只存 token 的 SHA-256）。
角色沿用治理层的 contributor / domain_reviewer / compliance_reviewer。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from src.evidence.governance import ROLE_COMPLIANCE, ROLE_CONTRIBUTOR, ROLE_DOMAIN

ALL_ROLES = (ROLE_CONTRIBUTOR, ROLE_DOMAIN, ROLE_COMPLIANCE)
SESSION_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_roles (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  PRIMARY KEY (user_id, role)
);
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
"""

_hasher = PasswordHasher()


def _now() -> str:
    """当前 UTC 时间（ISO 8601）。"""
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    """生成 Argon2 密码哈希。"""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """校验明文密码是否匹配 Argon2 哈希。"""
    try:
        return _hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def token_hash(token: str) -> str:
    """把 Bearer token 转为仅用于存储的 SHA-256 摘要。"""
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """裁掉密码哈希，返回可安全下发的账号信息。"""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "roles": list(user.get("roles") or []),
        "disabled": bool(user.get("disabled")),
    }


class AccountStore:
    """公共证据服务的独立账号库：`<root>/accounts.db`。"""

    def __init__(self, root: str | Path) -> None:
        """绑定账号库目录。"""
        self.root = Path(root)
        self.db_path = self.root / "accounts.db"

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

    @staticmethod
    def _validate_roles(roles: Iterable[str]) -> list[str]:
        """角色必须属于治理层三档；去重并保持稳定顺序。"""
        cleaned = sorted({str(role).strip() for role in roles if str(role).strip()})
        unknown = [role for role in cleaned if role not in ALL_ROLES]
        if unknown:
            raise ValueError("未知角色：" + "、".join(unknown))
        return cleaned

    def _roles(self, conn: sqlite3.Connection, user_id: str) -> list[str]:
        """读取单个用户的角色。"""
        rows = conn.execute(
            "SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (user_id,)
        ).fetchall()
        return [row["role"] for row in rows]

    def _user(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        """行 → 含角色的用户字典。"""
        user = dict(row)
        user["roles"] = self._roles(conn, row["id"])
        return user

    def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        display_name: str = "",
        roles: Iterable[str] = (ROLE_CONTRIBUTOR,),
    ) -> str:
        """创建账号并写入角色，返回用户 ID。"""
        username = username.strip()
        if not username:
            raise ValueError("username 必填")
        cleaned_roles = self._validate_roles(roles)
        user_id = uuid.uuid4().hex
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users "
                "(id, username, password_hash, display_name, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, username, password_hash, display_name.strip(), _now()),
            )
            conn.executemany(
                "INSERT INTO user_roles (user_id, role) VALUES (?, ?)",
                [(user_id, role) for role in cleaned_roles],
            )
        return user_id

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """按用户名读取账号（含密码哈希，仅供认证内部使用）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=?", (username.strip(),)
            ).fetchone()
            return self._user(conn, row) if row else None

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """用户名 + 密码认证；失败返回 None。"""
        user = self.get_user_by_username(username)
        if user is None or user["disabled"]:
            return None
        if not verify_password(user["password_hash"], password):
            return None
        return user

    def create_token(self, user_id: str, *, days: int = SESSION_DAYS) -> str:
        """签发新的 Bearer token；返回原始 token，库内只存哈希。"""
        raw = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tokens (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    token_hash(raw),
                    user_id,
                    now.isoformat(),
                    (now + timedelta(days=max(1, days))).isoformat(),
                ),
            )
        return raw

    def get_user_by_token(self, raw_token: str) -> dict[str, Any] | None:
        """按未过期 token 读取启用中的账号。"""
        if not raw_token:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT u.* FROM tokens t JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash=? AND t.expires_at>? AND u.disabled=0",
                (token_hash(raw_token), _now()),
            ).fetchone()
            return self._user(conn, row) if row else None

    def delete_token(self, raw_token: str) -> None:
        """吊销单个 token。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM tokens WHERE token_hash=?", (token_hash(raw_token),))

    def set_password(self, user_id: str, password_hash: str) -> None:
        """更新密码并吊销该账号的全部登录。"""
        with self._conn() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
            conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))

    def set_roles(self, user_id: str, roles: Iterable[str]) -> None:
        """整体替换账号角色。"""
        cleaned_roles = self._validate_roles(roles)
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
                raise ValueError("账号不存在")
            conn.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
            conn.executemany(
                "INSERT INTO user_roles (user_id, role) VALUES (?, ?)",
                [(user_id, role) for role in cleaned_roles],
            )

    def set_disabled(self, user_id: str, disabled: bool) -> None:
        """禁用或恢复账号；禁用时同时吊销 token。"""
        with self._conn() as conn:
            conn.execute("UPDATE users SET disabled=? WHERE id=?", (1 if disabled else 0, user_id))
            if disabled:
                conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))

    def list_users(self) -> list[dict[str, Any]]:
        """按用户名列出账号（不含密码哈希）。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
            return [public_user(self._user(conn, row)) for row in rows]
