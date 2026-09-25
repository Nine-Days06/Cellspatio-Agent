"""CLI: python -m src.cli.replay <run_id>"""
from __future__ import annotations

import argparse
import json

from src.control.replay import replay_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a CellSpatio analysis run")
    parser.add_argument("run_id", help="Run ID to replay (snapshot name)")
    parser.add_argument("--repo", default=None, help="Repository root (default: cwd)")
    args = parser.parse_args(argv)

    result = replay_run(args.run_id, repo_root=args.repo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())