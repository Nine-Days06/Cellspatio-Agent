"""启动公共证据服务：python -m src.public_api --root <data>。"""

from __future__ import annotations

import argparse
import os

import uvicorn

from src.public_api.app import create_public_app


def main(argv: list[str] | None = None) -> None:
    """解析参数并启动 uvicorn。"""
    parser = argparse.ArgumentParser(description="CellSpatio 公共证据服务")
    parser.add_argument("--host", default=os.environ.get("CELLSPATIO_PUBLIC_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("CELLSPATIO_PUBLIC_PORT", "8700"))
    )
    parser.add_argument(
        "--root", default=os.environ.get("CELLSPATIO_PUBLIC_DATA_ROOT") or os.getcwd()
    )
    args = parser.parse_args(argv)
    uvicorn.run(create_public_app(args.root), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
