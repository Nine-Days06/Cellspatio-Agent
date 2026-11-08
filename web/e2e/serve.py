"""Playwright 用的真实服务：临时 SQLite + 两个固定账号，不碰正式数据。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

db_dir = ROOT / "cellspatio-e2e"
shutil.rmtree(db_dir, ignore_errors=True)
db_dir.mkdir(parents=True)
os.environ["CELLSPATIO_SESSIONS_DB"] = str(db_dir / "sessions.db")
os.environ["CELLSPATIO_COOKIE_SECURE"] = "0"
for name in ("CELLSPATIO_AUTH_DISABLED", "CELLSPATIO_SITE_URL"):
    os.environ.pop(name, None)

import uvicorn  # noqa: E402

from src.api.app import create_app  # noqa: E402
from src.api.auth import hash_password  # noqa: E402
from src.ui.session_store import SessionStore  # noqa: E402


class StubAgent:
    """登录/会话路由不读 agent；仅用于 create_app 的 state。"""


app = create_app(StubAgent())
store = SessionStore()
for username in ("alice", "bob"):
    if store.get_user_by_username(username) is None:
        store.create_user(username, hash_password("password123"), username)

uvicorn.run(app, host="127.0.0.1", port=8620, log_level="warning")
