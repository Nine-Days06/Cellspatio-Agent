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


def latest_pending_raw(store: SessionStore, session_id: str) -> PendingScript | None:
    """从库里反查最新一条带 pending_script 的消息（不做过期改写）。"""
    for msg in reversed(store.get_messages(session_id)):
        if msg.get("pending_script"):
            return msg["pending_script"]
    return None


def restore_pending(
    store: SessionStore, session_id: str, messages: list[dict[str, Any]]
) -> PendingScript | None:
    """会话加载时的待确认脚本过期语义。

    只看最新一条带 pending_script 的消息：pending → 落库置 expired 后按
    expired 返回；expired → 原样返回；其他状态 → None。
    """
    for msg in reversed(messages):
        pending = msg.get("pending_script")
        if not pending:
            continue
        status = pending.get("status")
        if status in (STATUS_PENDING, STATUS_EXPIRED):
            if status == STATUS_PENDING:
                store.set_script_status(session_id, STATUS_EXPIRED)
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


def confirm_script(agent: Any, store: SessionStore, session_id: str, action: str) -> dict[str, Any]:
    """确认或取消最新 pending 脚本，并落一条助手消息。"""
    pending = latest_pending_raw(store, session_id)
    if pending is None or pending.get("status") != STATUS_PENDING:
        raise ConfirmFlowError(ERR_NO_PENDING_SCRIPT, "没有待确认的脚本")

    if action == ACTION_SCRIPT_CANCEL:
        store.set_script_status(session_id, SCRIPT_STATUS_BY_ACTION[action])
        content = "已取消本次脚本执行。"
        results = None
    elif action == ACTION_SCRIPT_CONFIRM:
        result = agent.execute_confirmed_script(
            pending.get("analysis_type"),
            pending.get("params") or {},
            pending.get("script", ""),
            method_context=pending.get("method_context"),
        )
        store.set_script_status(session_id, SCRIPT_STATUS_BY_ACTION[action])
        content = build_assistant_message(result)["content"]
        results = result.get("results")
    else:
        raise ConfirmFlowError(ERR_NOT_SCRIPT_ACTION, f"不是脚本动作: {action}")

    store.append_message(session_id, "assistant", content, results=results)
    return store.get_messages(session_id)[-1]


def _asset_key(asset: dict[str, Any], source: str) -> tuple[str, Any]:
    """已下载资产的去重键 (source, asset_id)；历史记录缺 source 字段时用请求 source。"""
    return (asset.get("source") or source, asset.get("asset_id"))


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
        session = store.get_session(session_id) or {}
        assets = list(session.get("downloaded_assets") or [])
        key = _asset_key(asset, source)
        # 重复确认：下载可重复，但同一资产只落库一次（避免污染 run_turn 上下文）
        if key not in {_asset_key(a, source) for a in assets}:
            assets.append(asset)
            store.update_session_meta(session_id, downloaded_assets=assets)
        content = f"已下载 {asset_id} → `{asset.get('access_path')}`"
    else:
        content = result.get("message", "数据下载未完成。")

    store.append_message(session_id, "assistant", content)
    return store.get_messages(session_id)[-1]
