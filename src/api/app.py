"""FastAPI 应用工厂：挂载 API 路由，注册全局异常兜底，按需托管前端构建产物。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api import (
    routes_chat,
    routes_clusters,
    routes_input,
    routes_markers,
    routes_meta,
    routes_pca,
    routes_projects,
    routes_qc,
    routes_sessions,
)
from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore

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
        content={"detail": "服务内部错误"},
    )


def create_app(agent: Any) -> FastAPI:
    """挂载全部 API 路由；agent 与 store factory 挂到 app.state。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """单进程启动只标记旧过滤任务中断，不恢复执行。"""
        app.state.store_factory().stages.interrupt_all()
        yield

    app = FastAPI(title="CellSpatio API", lifespan=lifespan)
    app.state.agent = agent
    app.state.store_factory = SessionStore
    recorder = getattr(getattr(agent, "workflow_manager", None), "workflow_recorder", None)
    app.state.repo_root = Path(recorder.repo_root) if recorder else Path.cwd()
    # 惰性读取，替换 app.state.store_factory 后 roots 的会话查询立即跟随。
    app.state.analysis_roots = AnalysisRoots(
        lambda: app.state.store_factory(),
        app.state.repo_root,
    )
    app.include_router(routes_sessions.router)
    app.include_router(routes_projects.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_meta.router)
    app.include_router(routes_input.router)
    app.include_router(routes_qc.router)
    app.include_router(routes_pca.router)
    app.include_router(routes_clusters.router)
    app.include_router(routes_markers.router)
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
