"""FastAPI 应用工厂：挂载 API 路由，并按需托管前端构建产物。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api import routes_sessions

# 前端构建产物目录。计划 1 阶段尚不存在，守卫保证无前端也能起服务；
# 抽成模块常量是为了让 T4 测试能 monkeypatch 覆盖「存在 / 不存在」两条分支。
_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(agent: Any) -> FastAPI:
    """构建 FastAPI 应用；agent 挂到 app.state，路由经 request.app.state.agent 取用。

    本任务只挂 routes_sessions；T5 加 routes_chat，T7 加 routes_meta，
    每次都改这一处，避免预先 import 尚未存在的模块。
    """
    app = FastAPI(title="CellSpatio API")
    app.state.agent = agent
    app.include_router(routes_sessions.router)
    if _WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_WEB_DIST, html=True))
    return app