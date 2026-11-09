"""会话绑定的本地数据入口：不经过 LLM，直接校验输入并运行固定 QC。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.api.chat_service import _persist, is_qc_stage, register_qc_result, session_lock
from src.api.events import wire_message
from src.control.chat_messages import build_assistant_message
from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["input"])
logger = logging.getLogger(__name__)


class LocalInputRequest(BaseModel):
    """本地输入只接受路径；格式由后端校验，不接受脚本或阈值。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=4096)


@router.post("/local-input")
def register_local_input(
    session_id: str, body: LocalInputRequest, request: Request
) -> dict[str, Any]:
    """登记本机 10x 目录/counts CSV 并运行固定 QC；之后仍走人工 gate。

    shortcut: 接受任意本机路径，仅适用于 loopback 客户端；多用户服务端需先加认证与路径白名单。
    """
    agent = request.app.state.agent
    store = SessionStore()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")

    source = Path(body.path.strip().strip('"')).expanduser()
    if not source.exists():
        raise HTTPException(400, f"路径不存在：{source}")
    source = source.resolve()

    lock = session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "该会话已有任务在进行")
    try:
        result = agent.workflow_manager.run_analysis_for_agent(
            "single_cell",
            {"input_files": [str(source)]},
            {"last_user_input": f"本地数据：{source}"},
        )
        if not is_qc_stage(result):
            # 非法输入在 QC 之前返回，不落聊天消息，直接作为对话框错误。
            raise HTTPException(400, result.get("message") or "输入不是有效的单细胞数据")

        # 同一会话只保留最新本地输入；后续对话与重跑沿用该路径。
        assets = [
            asset
            for asset in session.get("downloaded_assets") or []
            if asset.get("source") != "local"
        ]
        assets.append(
            {
                "source": "local",
                "asset_id": source.name or str(source),
                "access_path": str(source),
            }
        )
        store.update_session_meta(session_id, downloaded_assets=assets)

        result = register_qc_result(agent, store, session_id, result)
        message = build_assistant_message(result)
        last = _persist(store, session_id, message["content"], results=result.get("results"))
        return {
            "message": wire_message(last),
            "session": store.get_session(session_id),
            "qc_run_id": (result.get("results") or {}).get("qc_run_id"),
            "status": result.get("status"),
        }
    except HTTPException:
        raise
    except (OSError, ValueError, sqlite3.Error) as exc:
        logger.warning("local input failed for %s: %s", session_id, exc)
        raise HTTPException(409, str(exc)) from exc
    finally:
        lock.release()
