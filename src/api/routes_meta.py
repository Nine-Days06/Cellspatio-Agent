"""GET /api/sidebar：侧边栏状态区（知识库统计 + 环境自检 + 压缩软警告）。

所有探测都 try/except 降级：自检失败只在响应里标红，绝不阻断聊天。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from src.control import compact
from src.ui.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["meta"])


def _kb_stats(agent: Any) -> dict[str, Any]:
    """知识库统计；不可用时降级为带 error 的字典。"""
    try:
        return agent.knowledge_client.get_statistics()
    except Exception as exc:  # noqa: BLE001 - UI 状态区容错
        logger.warning("kb statistics failed: %s", exc)
        return {"error": str(exc), "initialized": False}


def _env_status(agent: Any) -> dict[str, Any]:
    """环境自检：Rscript 可执行文件 + 知识库目录是否存在。"""
    rscript: str | None = None
    try:
        resolved = agent.r_executor._resolve_rscript()
        rscript = resolved if resolved and Path(resolved).exists() else None
    except Exception as exc:  # noqa: BLE001 - 自检失败不阻断
        logger.warning("rscript probe failed: %s", exc)
        rscript = None
    if rscript is None:
        rscript = shutil.which("Rscript")
    kb_path = str(agent.config.get("knowledge_dir", "./knowledge_base"))
    return {"rscript": rscript, "kb_path": kb_path, "kb_ok": Path(kb_path).exists()}


def _soft_warn(session_id: str | None) -> bool:
    """按会话当前上下文判断压缩软阈值；无会话或异常一律 False。"""
    if not session_id:
        return False
    try:
        store = SessionStore()
        if store.get_session(session_id) is None:
            return False
        return bool(compact.should_soft_warn(
            store.get_session(session_id).get("summary"),
            compact.prepare_history(store.get_messages(session_id)),
        ))
    except Exception as exc:  # noqa: BLE001 - 软警告探测失败按未触发处理
        logger.warning("soft warn probe failed: %s", exc)
        return False


@router.get("/sidebar")
def sidebar(request: Request, session_id: str | None = None) -> dict[str, Any]:
    """侧边栏状态端点：kb_stats + env + soft_warn，任何探测失败都不抛错。"""
    agent = request.app.state.agent
    return {
        "kb_stats": _kb_stats(agent),
        "env": _env_status(agent),
        "soft_warn": _soft_warn(session_id),
    }
