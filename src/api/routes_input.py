"""会话绑定的本地数据入口：不经过 LLM，直接校验输入并运行固定 QC。"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.api.auth import is_ephemeral_workspace
from src.api.chat_service import is_qc_stage, persist_outcome, register_qc_result
from src.api.deps import StoreDep
from src.api.events import wire_message
from src.api.session_locks import session_guard

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["input"])
logger = logging.getLogger(__name__)


class LocalInputRequest(BaseModel):
    """本地输入只接受路径；格式由后端校验，不接受脚本或阈值。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1, max_length=4096)


@router.post("/local-input")
def register_local_input(
    session_id: str, body: LocalInputRequest, request: Request, store: StoreDep
) -> dict[str, Any]:
    """登记本机 10x 目录/counts CSV 并运行固定 QC；之后仍走人工 gate。

    shortcut: 接受任意本机路径，仅适用于 loopback 客户端；多用户服务端需先加认证与路径白名单。
    """
    if is_ephemeral_workspace(request):
        raise HTTPException(403, "访客不能读取服务器本地路径，请使用上传入口")
    agent = request.app.state.agent
    # 404 优先于路径校验；锁内仍会重读，资产合并只用锁内快照。
    if store.get_session(session_id) is None:
        raise HTTPException(404, "会话不存在")
    source = Path(body.path.strip().strip('"')).expanduser()
    if not source.exists():
        raise HTTPException(400, f"路径不存在：{source}")
    source = source.resolve()

    with session_guard(session_id):
        # 锁内重查：会话可能刚被并发删除。
        if store.get_session(session_id) is None:
            raise HTTPException(404, "会话不存在")
        try:
            result = agent.workflow_manager.run_analysis_for_agent(
                "single_cell",
                {"input_files": [str(source)]},
                {"last_user_input": f"本地数据：{source}", "session_id": session_id},
            )
            if not is_qc_stage(result):
                # 非法输入在 QC 之前返回，不落聊天消息，直接作为对话框错误。
                raise HTTPException(400, result.get("message") or "输入不是有效的单细胞数据")

            result = register_qc_result(agent, store, session_id, result)
            if result.get("status") != "error":
                # 先登记 QC 再写资产：登记失败时不留"有资产、无 run"的半成品。
                # 同一会话只保留最新本地输入；替换在写事务内完成。
                store.replace_local_asset(
                    session_id,
                    {
                        "source": "local",
                        "asset_id": source.name or str(source),
                        "access_path": str(source),
                    },
                )
            result, last = persist_outcome(store, session_id, result)
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
