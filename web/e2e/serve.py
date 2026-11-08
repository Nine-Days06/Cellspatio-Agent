"""Playwright 用的真实服务：每次进程独立临时 SQLite，不碰正式数据。"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 先导入应用（触发 src.config.load_dotenv），再覆盖环境变量，否则本地 .env
# 会重新写回 SITE_URL / AUTH_DISABLED，测试配置随机器而变。
from src.api.app import create_app  # noqa: E402
from src.api.auth import hash_password  # noqa: E402
from src.ui.session_store import SessionStore  # noqa: E402

DIST_INDEX = ROOT / "web" / "dist" / "index.html"
if not DIST_INDEX.is_file():
    raise SystemExit(f"缺少前端构建产物：{DIST_INDEX}；请先运行 npm run build")

# Windows 继承 ACL 会禁止在 web/e2e 下建删子目录，统一放在仓库根的忽略目录。
runs_dir = ROOT / ".e2e-runs"
runs_dir.mkdir(parents=True, exist_ok=True)
run_dir = Path(tempfile.mkdtemp(prefix=f"{os.getpid()}-", dir=runs_dir))
atexit.register(shutil.rmtree, run_dir, True)

os.environ["CELLSPATIO_SESSIONS_DB"] = str(run_dir / "sessions.db")
os.environ["CELLSPATIO_COOKIE_SECURE"] = "0"
os.environ["CELLSPATIO_AUTH_DISABLED"] = "0"
os.environ["CELLSPATIO_SITE_URL"] = "http://127.0.0.1:8620"

import uvicorn  # noqa: E402


class StubAgent:
    """登录/会话路由不读 agent；仅用于 create_app 的 state。"""


app = create_app(StubAgent())
store = SessionStore()
for username in ("alice", "bob"):
    if store.get_user_by_username(username) is None:
        store.create_user(username, hash_password("password123"), username)

uvicorn.run(app, host="127.0.0.1", port=8620, log_level="warning")
