"""账号管理 CLI：手动开户（本期），后续自注册另接路由。

python -m src.cli.users create <username> [--admin]
python -m src.cli.users list
python -m src.cli.users passwd <username>
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3

import src.config  # noqa: F401 - 触发 .env 加载，让 CELLSPATIO_SESSIONS_DB 生效
from src.api.auth import hash_password
from src.ui.session_store import SessionStore


def _read_password(confirm: bool = True) -> str:
    """交互读取并校验至少 8 位的密码。"""
    password = getpass.getpass("密码（至少 8 位）: ")
    if len(password) < 8:
        raise SystemExit("密码至少 8 位")
    if confirm and getpass.getpass("再输入一次: ") != password:
        raise SystemExit("两次输入不一致")
    return password


def main(argv: list[str] | None = None) -> int:
    """执行用户管理子命令。"""
    parser = argparse.ArgumentParser(description="CellSpatio 用户管理")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="新建用户")
    create.add_argument("username")
    create.add_argument("--display-name", default="")
    create.add_argument("--admin", action="store_true")

    sub.add_parser("list", help="列出用户")

    passwd = sub.add_parser("passwd", help="重置密码")
    passwd.add_argument("username")

    args = parser.parse_args(argv)
    store = SessionStore()

    if args.command == "create":
        if store.get_user_by_username(args.username):
            raise SystemExit(f"用户已存在: {args.username}")
        try:
            user_id = store.create_user(
                args.username,
                hash_password(_read_password()),
                args.display_name,
                is_admin=args.admin,
            )
        except sqlite3.IntegrityError as exc:
            raise SystemExit(f"用户已存在: {args.username}") from exc
        print(f"已创建: {args.username} ({user_id})")
        return 0

    if args.command == "list":
        for user in store.list_users():
            role = "admin" if user["is_admin"] else "user"
            state = "disabled" if user["disabled"] else "active"
            print(
                f"{user['id']:<36} {user['username']:<24} {role:<6} {state:<8} {user['display_name']}"
            )
        return 0

    user = store.get_user_by_username(args.username)
    if user is None:
        raise SystemExit(f"用户不存在: {args.username}")
    store.update_user_password(user["id"], hash_password(_read_password()))
    store.delete_user_tokens(user["id"])
    print(f"已重置密码（旧登录全部失效）: {args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
