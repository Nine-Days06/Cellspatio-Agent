"""管理账户命令行：创建、列出、改密、禁用；不提供注册或找回密码。"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher

from src.ui.session_store import SessionStore

_hasher = PasswordHasher()
ADMIN_USERNAMES = ("admin1", "admin2", "admin3")


def _read_password(confirm: bool = True) -> str:
    """从终端读取密码；默认要求二次确认。"""
    first = getpass.getpass("密码: ")
    if not first:
        raise ValueError("密码不能为空")
    if confirm and getpass.getpass("确认密码: ") != first:
        raise ValueError("两次密码不一致")
    return first


def _create_account(
    store: SessionStore, root: Path, username: str, password: str
) -> dict[str, Any]:
    """创建永久工作区与账户，返回公开字段。"""
    if store.get_account_by_username(username):
        raise ValueError(f"账户已存在：{username}")
    wid = store.create_workspace(kind="persistent", label=username)
    workspace_root = (root / "data" / "workspaces" / wid).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    store.set_workspace_root(wid, str(workspace_root))
    account_id = store.create_account(
        username,
        _hasher.hash(password),
        username,
        wid,
        is_admin=True,
    )
    return {"id": account_id, "username": username, "workspace_id": wid}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CellSpatio 管理账户")
    parser.add_argument("--root", default=".", help="数据根（默认 cwd）")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建一个管理账户")
    create.add_argument("username")
    create.add_argument("--display-name", default=None)
    create.add_argument("--password-stdin", action="store_true")

    init = sub.add_parser("init-three", help="一次性创建 admin1-3，并输出初始密码")
    init.add_argument("--yes", action="store_true", help="确认写入账户与工作区")

    passwd = sub.add_parser("passwd", help="修改密码")
    passwd.add_argument("username")

    rename = sub.add_parser("rename", help="修改登录名")
    rename.add_argument("username")
    rename.add_argument("new_username")

    disable = sub.add_parser("disable", help="禁用账户")
    disable.add_argument("username")

    sub.add_parser("list", help="列出账户")

    args = parser.parse_args(argv)
    store = SessionStore()
    root = Path(args.root).resolve()
    try:
        if args.command == "list":
            print(json.dumps(store.list_accounts(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "create":
            password = getpass.getpass("密码: ") if args.password_stdin else _read_password()
            account = _create_account(store, root, args.username, password)
            if args.display_name:
                store.set_account_display_name(account["id"], args.display_name)
                account["display_name"] = args.display_name
            print(json.dumps({"status": "ok", **account}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "init-three":
            if not args.yes:
                raise ValueError("初始化三个账户需显式 --yes")
            output = []
            for username in ADMIN_USERNAMES:
                password = secrets.token_urlsafe(18)
                account = _create_account(store, root, username, password)
                output.append({**account, "initial_password": password})
            print(json.dumps({"status": "ok", "accounts": output}, ensure_ascii=False, indent=2))
            return 0
        account = store.get_account_by_username(args.username)
        if account is None:
            raise ValueError("账户不存在")
        if args.command == "passwd":
            store.set_account_password(account["id"], _hasher.hash(_read_password()))
        elif args.command == "rename":
            if store.get_account_by_username(args.new_username):
                raise ValueError(f"账户已存在：{args.new_username}")
            store.set_account_username(account["id"], args.new_username)
            store.set_account_display_name(account["id"], args.new_username)
            account["username"] = args.new_username
        elif args.command == "disable":
            store.set_account_disabled(account["id"], True)
        print(json.dumps({"status": "ok", "username": account["username"]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
