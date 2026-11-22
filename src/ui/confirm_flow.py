"""确认协议状态机：动作、状态与待确认脚本流转的唯一实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.chat_messages import build_assistant_message
from src.ui.confirm_protocol import (
    ACTION_SCRIPT_CANCEL,
    ACTION_SCRIPT_CONFIRM,
    SCRIPT_STATUS_BY_ACTION,
    STATUS_EXPIRED,
    STATUS_PENDING,
    PendingScript,
)

if TYPE_CHECKING:
    from src.ui.session_store import SessionStore

ERR_NO_PENDING_SCRIPT = "no_pending_script"
ERR_MISSING_SOURCE = "missing_source"
ERR_UNKNOWN_SOURCE = "unknown_source"
ERR_NOT_SCRIPT_ACTION = "not_script_action"


class ConfirmFlowError(ValueError):
    """确认状态机业务错误；由 API 路由映射为 HTTP 状态码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def restore_pending(
    store: SessionStore, session_id: str, messages: list[dict[str, Any]]
) -> PendingScript | None:
    """会话加载时的待确认脚本过期语义。

    只看最新一条带 pending_script 的消息：pending → 把**这一条**（按 id）落库
    置 expired 后按 expired 返回；expired → 原样返回；其他状态 → None。
    """
    for msg in reversed(messages):
        pending = msg.get("pending_script")
        if not pending:
            continue
        status = pending.get("status")
        if status in (STATUS_PENDING, STATUS_EXPIRED):
            if status == STATUS_PENDING:
                # 必须按 id 定位：并发新建的卡可能已是最新一条，不能被这里误废。
                store.set_script_status(session_id, STATUS_EXPIRED, message_id=msg["id"])
            return {
                "script": pending.get("script", ""),
                "analysis_type": pending.get("analysis_type"),
                "params": pending.get("params") or {},
                "method_context": pending.get("method_context"),
                "user_request": pending.get("user_request", ""),
                "status": STATUS_EXPIRED,
            }
        # 只看最新一条 pending_script，更早的不再考虑
        return None
    return None


def confirm_script(
    agent: Any,
    store: SessionStore,
    session_id: str,
    action: str,
    *,
    message_id: int | None = None,
) -> dict[str, Any]:
    """确认或取消指定（缺省最新）pending 脚本，并落一条助手消息。

    先用 CAS 把卡片改到终态再执行：同一张卡被两个标签页同时确认时，只有一个能拿到执行权。
    """
    if action not in SCRIPT_STATUS_BY_ACTION or action not in (
        ACTION_SCRIPT_CONFIRM,
        ACTION_SCRIPT_CANCEL,
    ):
        raise ConfirmFlowError(ERR_NOT_SCRIPT_ACTION, f"不是脚本动作: {action}")

    status = SCRIPT_STATUS_BY_ACTION[action]
    claimed = store.claim_script(session_id, status, message_id=message_id)
    if claimed is None:
        raise ConfirmFlowError(ERR_NO_PENDING_SCRIPT, "没有待确认的脚本")
    claimed_id, pending = claimed
    token = pending.get("claim_token") or ""

    if action == ACTION_SCRIPT_CANCEL:
        content = "已取消本次脚本执行。"
        results = None
    else:
        try:
            result = agent.execute_confirmed_script(
                pending.get("analysis_type"),
                pending.get("params") or {},
                pending.get("script", ""),
                method_context=pending.get("method_context"),
                session_id=session_id,
            )
        except Exception:
            # 执行失败不能把卡片留在终态，否则用户既看不到结果也无法重试。
            store.release_script(session_id, claimed_id, token)
            raise
        if result.get("status") == "error":
            # 修复次数用尽/产物缺失以正常返回值报告失败，同样要把卡片退回 pending。
            store.release_script(session_id, claimed_id, token)
        content = build_assistant_message(result)["content"]
        results = result.get("results")

    return store.append_message(session_id, "assistant", content, results=results)


def confirm_data(
    agent: Any,
    store: SessionStore,
    session_id: str,
    source: str | None,
    asset_id: str | None,
    query: str | None = None,
) -> dict[str, Any]:
    """下载确认：去重落库下载资产并落一条助手消息。"""
    if not source or not asset_id:
        raise ConfirmFlowError(ERR_MISSING_SOURCE, "缺少 source 或 asset_id")

    try:
        result = agent.confirm_and_download(source, asset_id, query=query or "")
    except KeyError as exc:  # fetcher_registry.get 未注册 source
        raise ConfirmFlowError(ERR_UNKNOWN_SOURCE, f"未知数据源: {source}") from exc
    asset = result.get("asset")
    if asset:
        # 重复确认：下载可重复，但同一资产只落库一次（避免污染 run_turn 上下文）。
        # 去重与追加在写事务内完成，避免并发 data_confirm 整列覆盖。
        store.append_downloaded_asset(session_id, asset, source=source)
        content = f"已下载 {asset_id} → `{asset.get('access_path')}`"
    else:
        content = result.get("message", "数据下载未完成。")

    return store.append_message(session_id, "assistant", content)
