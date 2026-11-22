"""账号认证的常数时间失败路径。"""

from __future__ import annotations

from src.evidence import accounts
from src.evidence.accounts import AccountStore, hash_password

PASSWORD = "password123"


def test_unknown_username_still_runs_password_verification(tmp_path, monkeypatch):
    """未知账号也必须走一次 Argon2，避免用响应时间枚举用户名。"""
    store = AccountStore(tmp_path / "public_evidence")
    calls: list[str] = []
    monkeypatch.setattr(accounts, "verify_password", lambda h, p: calls.append(h) or False)

    assert store.authenticate("nobody", PASSWORD) is None
    assert calls == [accounts._DUMMY_HASH]


def test_disabled_user_also_verifies_dummy_hash(tmp_path, monkeypatch):
    """禁用账号与不存在账号对外不可区分。"""
    store = AccountStore(tmp_path / "public_evidence")
    user = store.create_user("alice", hash_password(PASSWORD), roles=())
    store.set_disabled(user, True)
    calls: list[str] = []
    monkeypatch.setattr(accounts, "verify_password", lambda h, p: calls.append(h) or False)

    assert store.authenticate("alice", PASSWORD) is None
    assert calls == [accounts._DUMMY_HASH]
