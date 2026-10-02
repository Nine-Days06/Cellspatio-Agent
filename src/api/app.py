"""FastAPI 应用工厂：挂载 API 路由，注册全局异常兜底，按需托管前端构建产物。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api import routes_chat, routes_meta, routes_sessions

logger = logging.getLogger(__name__)

# 前端构建产物目录。计划 1 阶段尚不存在，守卫保证无前端也能起服务；
# 抽成模块常量是为了让 T4 测试能 monkeypatch 覆盖「存在 / 不存在」两条分支。
_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未结构化异常统一兜底：500 + {"detail":...} JSON（与 4xx/502 契约一致）。

    注册在 Exception 上只拦截未被 HTTPException 处理的异常，4xx 照常走
    FastAPI 默认处理；ServerErrorMiddleware 仍会 re-raised，由 uvicorn 记日志。
    """
    logger.exception("unhandled API error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc) or type(exc).__name__},
    )


def create_app(agent: Any) -> FastAPI:
    """构建 FastAPI 应用；agent 挂到 app.state，路由经 request.app.state.agent 取用。

    挂载 routes_sessions + routes_chat + routes_meta；新增路由模块时改这一处。
    """
    app = FastAPI(title="CellSpatio API")
    app.state.agent = agent
    app.include_router(routes_sessions.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_meta.router)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    # router 必须先于 StaticFiles 挂载，否则 /api/* 会被 SPA 回退拦截
    if _WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=_WEB_DIST, html=True))
    _warm_up_ollama()
    return app


def _warm_up_ollama() -> None:
    """构建应用时按需预热 Ollama（仅 OLLAMA_EAGER_START=1 时动作）。

    放在 create_app 末尾而非 uvicorn lifespan：CLI 路径（`python -m src.api`）与
    测试用 ASGI 客户端都会经过这里，无需另找挂载点。内部是守护线程 + 不抛异常，
    因此既不阻塞应用就绪，预热失败也只等价于退回按需唤起。
    """
    try:
        from src.knowledge.ollama_runtime import warm_up_async

        warm_up_async()
    except Exception as exc:  # noqa: BLE001 - 预热不该影响应用启动
        logger.warning("ollama warm-up skipped: %s", exc)
