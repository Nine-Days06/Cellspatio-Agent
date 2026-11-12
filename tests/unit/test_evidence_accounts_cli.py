"""公共证据账号 CLI：开户、角色替换与禁用。"""

from __future__ import annotations

from src.cli import evidence_accounts
from src.evidence.accounts import AccountStore

PASSWORD = "password123"


def _store(tmp_path) -> AccountStore:
    """账号 CLI 的固定落点。"""
    return AccountStore(tmp_path / "public_evidence")


def test_account_cli_create_list_roles_and_disable(tmp_path, monkeypatch, capsys):
    """CLI 命令必须落到同一个账号库并即时生效。"""
    monkeypatch.setattr(evidence_accounts.getpass, "getpass", lambda prompt="": PASSWORD)
    root = str(tmp_path)

    assert (
        evidence_accounts.main(
            [
                "--root",
                root,
                "create",
                "alice",
                "--role",
                "contributor",
                "--role",
                "domain_reviewer",
            ]
        )
        == 0
    )
    created = _store(tmp_path).get_user_by_username("alice")
    assert created is not None
    assert created["roles"] == ["contributor", "domain_reviewer"]

    assert evidence_accounts.main(["--root", root, "roles", "alice", "compliance_reviewer"]) == 0
    assert _store(tmp_path).get_user_by_username("alice")["roles"] == ["compliance_reviewer"]

    assert evidence_accounts.main(["--root", root, "disable", "alice"]) == 0
    assert bool(_store(tmp_path).get_user_by_username("alice")["disabled"]) is True

    assert evidence_accounts.main(["--root", root, "list"]) == 0
    assert "alice" in capsys.readouterr().out
