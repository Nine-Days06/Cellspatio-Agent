"""临时工作区运维：清理到期工作区。"""

from __future__ import annotations

import argparse
import json

from src.ui.session_store import SessionStore
from src.ui.workspace_cleanup import cleanup_expired_workspaces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CellSpatio 工作区运维")
    sub = parser.add_subparsers(dest="command", required=True)
    cleanup = sub.add_parser("cleanup", help="删除到期临时工作区")
    cleanup.add_argument("--root", default=None, help="数据根（默认 cwd）")
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--yes", action="store_true", help="确认不可恢复的删除")
    args = parser.parse_args(argv)

    if args.command != "cleanup":
        parser.error("未知命令")
    if not args.dry_run and not args.yes:
        parser.error("实际删除需显式 --yes；请先运行 --dry-run")
    result = cleanup_expired_workspaces(
        SessionStore(),
        args.root or ".",
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
