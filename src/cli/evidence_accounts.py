"""公共证据服务账号管理：开户、改密、角色与禁用。

python -m src.cli.evidence_accounts --root <data> create <username> --role contributor
python -m src.cli.evidence_accounts --root <data> list
python -m src.cli.evidence_accounts --root <data> roles <username> domain_reviewer
python -m src.cli.evidence_accounts --root <data> passwd <username>
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3

from src.evidence.accounts import ALL_ROLES, AccountStore, hash_password
from src.evidence.governance import ROLE_CONTRIBUTOR, public_evidence_dir


def _store(root: str | None) -> AccountStore:
    """`--root` 是数据根；账号库固定在其下的 `public_evidence/`。"""
    from pathlib import Path

    base = Path(root or Path.cwd()).resolve()
    return AccountStore(public_evidence_dir(base))


def _read_password(confirm: bool = True) -> str:
    """交互读取并校验至少 8 位的密码。"""
    password = getpass.getpass("密码（至少 8 位）: ")
    if len(password) < 8:
        raise SystemExit("密码至少 8 位")
    if confirm and getpass.getpass("再输入一次: ") != password:
        raise SystemExit("两次输入不一致")
    return password


def _user_id(store: AccountStore, username: str) -> str:
    """按用户名取 ID，缺失即退出。"""
    user = store.get_user_by_username(username)
    if user is None:
        raise SystemExit(f"账号不存在: {username}")
    return user["id"]


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行账号管理命令。"""
    parser = argparse.ArgumentParser(description="CellSpatio 公共证据服务账号管理")
    parser.add_argument("--root", default=None, help="数据根（默认 cwd）")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="新建账号")
    create.add_argument("username")
    create.add_argument("--display-name", default="")
    create.add_argument("--role", action="append", default=None, choices=ALL_ROLES)

    sub.add_parser("list", help="列出账号")

    passwd = sub.add_parser("passwd", help="重置密码")
    passwd.add_argument("username")

    roles = sub.add_parser("roles", help="整体替换角色")
    roles.add_argument("username")
    roles.add_argument("role", nargs="+", choices=ALL_ROLES)

    disable = sub.add_parser("disable", help="禁用账号并吊销登录")
    disable.add_argument("username")

    enable = sub.add_parser("enable", help="恢复账号")
    enable.add_argument("username")

    args = parser.parse_args(argv)
    store = _store(args.root)
    try:
        if args.command == "create":
            if store.get_user_by_username(args.username):
                raise SystemExit(f"账号已存在: {args.username}")
            user_id = store.create_user(
                args.username,
                hash_password(_read_password()),
                display_name=args.display_name,
                roles=args.role or [ROLE_CONTRIBUTOR],
            )
            print(f"已创建: {args.username} ({user_id})")
        elif args.command == "list":
            for user in store.list_users():
                state = "disabled" if user["disabled"] else "active"
                print(
                    f"{user['id']:<32} {user['username']:<20} {state:<8} {','.join(user['roles'])}"
                )
        elif args.command == "passwd":
            store.set_password(_user_id(store, args.username), hash_password(_read_password()))
            print(f"已重置密码（旧登录全部失效）: {args.username}")
        elif args.command == "roles":
            store.set_roles(_user_id(store, args.username), args.role)
            print(f"已更新角色: {args.username} → {','.join(args.role)}")
        else:
            store.set_disabled(_user_id(store, args.username), args.command == "disable")
            print(f"已{'禁用' if args.command == 'disable' else '恢复'}: {args.username}")
    except sqlite3.IntegrityError as exc:
        raise SystemExit(f"账号已存在: {args.username}") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
