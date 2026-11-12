"""CLI: 公共证据治理假服务器（票据 17）。

python -m src.cli.evidence_governance submit envelope.json
python -m src.cli.evidence_governance review <evidence_id> --actor a --role domain_reviewer \\
    --action domain_pass --check method_complete=true ...
python -m src.cli.evidence_governance withdraw <evidence_id> --actor a --role contributor --reason ...
python -m src.cli.evidence_governance search --query TP53 --gate qc_review
python -m src.cli.evidence_governance show <evidence_id> [--reviewer]
python -m src.cli.evidence_governance events <evidence_id>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.evidence.governance import (
    ACTION_COMPLIANCE_NEEDS_CHANGES,
    ACTION_COMPLIANCE_PASS,
    ACTION_COMPLIANCE_REJECT,
    ACTION_DOMAIN_NEEDS_CHANGES,
    ACTION_DOMAIN_PASS,
    ACTION_DOMAIN_REJECT,
    ROLE_COMPLIANCE,
    ROLE_CONTRIBUTOR,
    ROLE_DOMAIN,
    GovernanceStore,
    SubmissionEnvelope,
)


def _store(root: str | None) -> GovernanceStore:
    """`--root` 是数据根；公共库固定在其下的 `public_evidence/`。"""
    base = Path(root or Path.cwd()).resolve()
    return GovernanceStore(base / "public_evidence")


def _parse_checks(items: list[str]) -> dict[str, bool]:
    """把 `key=true` 列表解析成清单。"""
    checklist: dict[str, bool] = {}
    for item in items:
        key, _, raw = item.partition("=")
        if not key or not raw:
            raise ValueError("--check 必须是 key=true/false")
        checklist[key] = raw.strip().lower() in ("1", "true", "yes", "on")
    return checklist


def _load_envelope(path: str) -> SubmissionEnvelope:
    """读取并解析提交信封。"""
    data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    return SubmissionEnvelope.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行治理命令。"""
    parser = argparse.ArgumentParser(description="CellSpatio 公共证据治理假服务器")
    parser.add_argument("--root", default=None, help="数据根（默认 cwd）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("envelope")

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("evidence_id")
    review_parser.add_argument("--actor", required=True)
    review_parser.add_argument("--role", required=True, choices=(ROLE_DOMAIN, ROLE_COMPLIANCE))
    review_parser.add_argument(
        "--action",
        required=True,
        choices=(
            ACTION_DOMAIN_PASS,
            ACTION_DOMAIN_NEEDS_CHANGES,
            ACTION_DOMAIN_REJECT,
            ACTION_COMPLIANCE_PASS,
            ACTION_COMPLIANCE_NEEDS_CHANGES,
            ACTION_COMPLIANCE_REJECT,
        ),
    )
    review_parser.add_argument("--check", action="append", default=[], metavar="KEY=TRUE")
    review_parser.add_argument("--reason", default=None)

    withdraw_parser = subparsers.add_parser("withdraw")
    withdraw_parser.add_argument("evidence_id")
    withdraw_parser.add_argument("--actor", required=True)
    withdraw_parser.add_argument(
        "--role", required=True, choices=(ROLE_CONTRIBUTOR, ROLE_COMPLIANCE)
    )
    withdraw_parser.add_argument("--reason", required=True)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("--query", default="")
    search_parser.add_argument("--gate", default=None)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("evidence_id")
    show_parser.add_argument(
        "--reviewer", action="store_true", help="复核者视角：可读 pending/needs_changes/rejected"
    )
    show_parser.add_argument("--actor", default=None)
    show_parser.add_argument("--role", default=None, choices=(ROLE_DOMAIN, ROLE_COMPLIANCE))

    events_parser = subparsers.add_parser("events")
    events_parser.add_argument("evidence_id")

    args = parser.parse_args(argv)
    try:
        store = _store(args.root)
        if args.command == "submit":
            result: Any = store.submit(_load_envelope(args.envelope))
        elif args.command == "review":
            result = store.review(
                args.evidence_id,
                actor_id=args.actor,
                role=args.role,
                action=args.action,
                checklist=_parse_checks(args.check),
                reason=args.reason,
            )
        elif args.command == "withdraw":
            result = store.withdraw(
                args.evidence_id,
                actor_id=args.actor,
                role=args.role,
                reason=args.reason,
            )
        elif args.command == "search":
            result = store.search(args.query, args.gate)
        elif args.command == "show":
            result = store.get(
                args.evidence_id,
                include_unaccepted=args.reviewer,
                viewer_actor=args.actor,
                viewer_role=args.role,
            )
        else:
            result = store.events(args.evidence_id)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
