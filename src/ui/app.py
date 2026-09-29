import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# 保证以 `streamlit run src/ui/app.py` 启动时，模块级导入也能 `import src.*`
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.control import compact
from src.control.chat_messages import (
    build_assistant_message as _build_assistant_message,
)
from src.control.chat_messages import (
    format_script_confirmation as _format_script_confirmation,
)
from src.control.chat_messages import (
    make_llm_summarize as _make_llm_summarize,
)
from src.control.chat_messages import (
    summarize_result as _summarize_result,
)
from src.ui.components import render_analysis_results, render_starter_presets
from src.ui.session_store import SessionStore


def _get_store() -> SessionStore:
    """每次新建会话存储（短连接模型，无缓存必要）。"""
    return SessionStore()


def run_prompt(agent: Any, prompt: str) -> None:
    """统一执行：显示用户消息 → workflow → 渲染/确认流 → 入历史"""
    st.session_state["messages"].append({"role": "user", "content": prompt})
    st.session_state["_scroll_pending"] = True
    with st.chat_message("user"):
        st.markdown(prompt)

    # 上下文压缩：准备历史 → 超阈值摘要 → 取最近窗口
    prepared = compact.prepare_history(st.session_state["messages"][:-1])
    summary, offset = compact.maybe_compact(
        st.session_state.get("chat_summary"),
        st.session_state.get("summary_upto", 0),
        prepared,
        _make_llm_summarize(agent),
    )
    st.session_state["chat_summary"] = summary
    st.session_state["summary_upto"] = offset
    _older, recent = compact.select_recent(prepared[offset:])

    context = {
        "history": recent,
        "summary": summary,
        "downloaded_assets": st.session_state.get("downloaded_assets", []),
        "last_user_input": prompt,
    }

    with st.chat_message("assistant"), st.spinner("思考中..."):
        result = agent.execute_workflow(prompt, context=context)

    if result.get("status") == "needs_script_confirmation":
        st.session_state["pending_script"] = {
            "script": result.get("script", ""),
            "analysis_type": result.get("analysis_type"),
            "params": result.get("params") or {},
            "method_context": result.get("method_context"),
            "user_request": prompt,
            "status": "pending",
        }
        st.session_state["awaiting_script_confirmation"] = True
        st.session_state["messages"].append(
            {
                "role": "assistant",
                "content": _format_script_confirmation(result),
                "pending_script": dict(st.session_state["pending_script"]),
            }
        )
        # 不在此渲染：统一由 create_app 的重放 + 条件组件（单调用点）负责
        return

    if result.get("type") == "fetch_data" and result.get("status") == "needs_confirmation":
        st.session_state["fetch_candidates"] = result.get("candidates", [])
        st.session_state["fetch_query"] = result.get("query", "")
        st.session_state["awaiting_confirmation"] = True
        st.session_state["messages"].append(
            {"role": "assistant", "content": result.get("message", "找到候选数据集，请选择要下载的项：")}
        )
        # 不在此渲染：重放历史显示提示语，create_app 的候选选择器负责交互
        return

    _render_chat_result(result)
    st.session_state["messages"].append(_build_assistant_message(result))


# :has() 选择器依赖 Streamlit 1.64 的 stChatMessage DOM 结构，升级 Streamlit 时需复验
_BUBBLE_CSS = """
<style>
div[data-testid="stChatMessage"]:has(.cs-bubble-user) {
    background: #2563eb;
    border-radius: 4px 16px 16px 4px;
    padding: 8px 14px;
    align-self: flex-end;
    margin-left: auto;
    max-width: 80%;
}
div[data-testid="stChatMessage"]:has(.cs-bubble-user) p,
div[data-testid="stChatMessage"]:has(.cs-bubble-user") li {
    color: #ffffff;
}
div[data-testid="stChatMessage"]:has(.cs-bubble-assistant) {
    background: #e9eaed;
    border-radius: 16px 4px 4px 16px;
    padding: 8px 14px;
    align-self: flex-start;
    margin-right: auto;
    max-width: 80%;
}
.cs-bubble { display: none; }
p:has(> .cs-bubble) { display: none; }
</style>
"""


def _render_bubble(message: dict[str, Any], idx: int) -> None:
    """按角色渲染左右气泡；results 在列外全宽渲染。"""
    role = message.get("role", "assistant")
    marker = f'<span class="cs-bubble cs-bubble-{role}"></span>'
    if role == "user":
        cols = st.columns([1, 4])
        target = cols[1]
    else:
        cols = st.columns([4, 1])
        target = cols[0]
    with target, st.chat_message(role):
        st.html(marker, unsafe_allow_javascript=True)
        st.markdown(message.get("content") or "")
    # 图表/统计在列外，保持全宽
    if message.get("results"):
        render_analysis_results(message["results"], key_prefix=f"hist_{idx}_")


def _render_scroll() -> None:
    """有待滚动标记时注入一次性滚动脚本（st.html 安全执行）。"""
    if not st.session_state.pop("_scroll_pending", False):
        return
    st.html(
        "<script>"
        "const cs = parent.document.querySelectorAll('[data-testid=\"stChatMessage\"]'); const c = cs[cs.length - 1];"
        "if (c) { c.scrollIntoView({block: 'end'}); }"
        "window.scrollTo(0, document.body.scrollHeight);"
        "</script>",
        unsafe_allow_javascript=True,
    )


def create_app(agent: Any):
    """创建 Streamlit 应用"""
    store = _get_store()

    # 会话状态初始化（必须早于侧边栏与历史渲染）
    _init_session_state(store)
    _persist_new_messages(store)

    st.title("CellSpatio 单细胞与时空组学分析智能体")
    st.caption("单细胞与空间/时序组学分析 · LightRAG 知识问答")

    # 软阈值提醒（不阻断）
    if compact.should_soft_warn(
        st.session_state.get("chat_summary"),
        compact.prepare_history(st.session_state["messages"]),
    ):
        st.warning(
            f"会话上下文已接近上限（约 {compact.SOFT_TRIGGER:,} token），"
            "继续对话将自动压缩早期记忆。"
        )

    # 侧边栏：会话列表 + 设置 + 知识库状态
    with st.sidebar:
        _render_session_sidebar(store)
        st.header("设置")
        external_api = st.checkbox("启用外部 API 查询", value=False)
        if external_api:
            st.info("外部 API 已启用，将查询最新文献和数据库。")

        st.header("知识库状态")
        if st.button("刷新", key="kb_stats_refresh"):
            st.session_state.pop("kb_stats", None)
            st.rerun()
        if "kb_stats" not in st.session_state:
            try:
                st.session_state.kb_stats = agent.knowledge_client.get_statistics()
            except Exception as e:  # noqa: BLE001 - UI 容错，知识库不可用时降级展示
                st.session_state.kb_stats = {"error": str(e), "initialized": False}
                st.error(f"知识库状态获取失败: {e}")
        st.json(st.session_state.kb_stats)

    # 气泡样式（一次注入）
    st.markdown(_BUBBLE_CSS, unsafe_allow_html=True)

    # 显示聊天历史（带分析结果的消息在重放时重新渲染图表）
    for idx, message in enumerate(st.session_state["messages"]):
        _render_bubble(message, idx)

    # 候选选择器（在聊天输入之前渲染，避免重复渲染问题）
    if st.session_state.get("awaiting_confirmation"):
        _render_candidate_selector(agent)

    # 脚本确认（在聊天输入之前渲染）
    if st.session_state.get("awaiting_script_confirmation"):
        _render_script_confirmation(agent)

    # 预设起点（仅会话较空时展示）
    if len(st.session_state.messages) <= 1:
        clicked = render_starter_presets()
        if clicked:
            st.session_state.auto_prompt = clicked

    # 自动提示（预设/基因追问）优先于手动输入
    auto = st.session_state.pop("auto_prompt", None)
    if auto:
        run_prompt(agent, auto)
        st.rerun()
        return

    if prompt := st.chat_input("请输入您的问题或分析需求"):
        run_prompt(agent, prompt)
        st.rerun()

    _render_scroll()


def _render_candidate_selector(agent: Any):
    """渲染候选数据集选择器与确认下载按钮"""
    with st.chat_message("assistant"):
        candidates = st.session_state.get("fetch_candidates") or []
        if not candidates:
            st.session_state["awaiting_confirmation"] = False
            return
        label_map = {
            f"[{c['source']}] {c['asset_id']} - {c['title']}": c
            for c in candidates
        }
        choice = st.selectbox("选择要下载的数据集：", list(label_map.keys()))
        cand = label_map[choice]
        st.caption(f"推荐理由：{cand.get('reason', '—')}")
        if cand.get("description"):
            st.info(cand["description"])
        md = cand.get("metadata") or {}
        if md:
            st.markdown("**元数据对比（当前项）**")
            st.json(md)
        # 若存在多项，简易对比表
        if len(candidates) > 1:
            rows = []
            for c in candidates:
                row = {"id": c["asset_id"], "title": c["title"], "source": c["source"]}
                row.update(
                    {
                        k: c.get("metadata", {}).get(k, "")
                        for k in ("organism", "samples", "platform")
                    }
                )
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        if st.button("确认下载"):
            selected = label_map[choice]
            with st.spinner("下载中..."):
                result = agent.confirm_and_download(
                    selected['source'],
                    selected['asset_id'],
                    query=st.session_state.get("fetch_query", ""),
                )
            st.session_state["awaiting_confirmation"] = False
            # 持久化 downloaded_asset，供后续 run_prompt 组装 context
            if "asset" in result:
                st.session_state.setdefault("downloaded_assets", []).append(
                    result["asset"]
                )
            message = f"已下载 {selected['asset_id']} → `{result['asset']['access_path']}`"
            st.session_state["messages"].append({"role": "assistant", "content": message})
            st.rerun()


def _render_chat_result(result: dict[str, Any]) -> None:
    """按结果类型渲染聊天回复（文本与历史落盘同源，含来源段）"""
    content, _references = _summarize_result(result)
    with st.chat_message("assistant"):
        st.markdown(content)
        if result.get("results"):
            render_analysis_results(result["results"])


def _render_script_confirmation(agent: Any) -> None:
    """渲染脚本确认按钮（脚本本体由历史消息承载，此处不重复渲染）。"""
    pending = st.session_state.pending_script
    if not pending:
        st.session_state.awaiting_script_confirmation = False
        return
    expired = pending.get("status") == "expired"
    if expired:
        st.warning("脚本已过期，请点击「重新生成」后再确认执行")
    else:
        st.info("请审阅上方脚本，确认后执行")

    col1, col2 = st.columns(2)
    if col1.button("确认执行", type="primary", key="confirm_script_btn",
                   disabled=expired):
        with st.spinner("执行中..."):
            result = agent.execute_confirmed_script(
                pending["analysis_type"],
                pending["params"],
                pending["script"],
                method_context=pending.get("method_context"),
            )
        _set_script_status("confirmed")
        st.session_state.awaiting_script_confirmation = False
        st.session_state.pending_script = None
        _render_chat_result(result)
        st.session_state.messages.append(_build_assistant_message(result))
        st.rerun()
    if col2.button("取消", key="cancel_script_btn"):
        _set_script_status("cancelled")
        st.session_state.awaiting_script_confirmation = False
        st.session_state.pending_script = None
        st.session_state.messages.append(
            {"role": "assistant", "content": "已取消本次脚本执行。"}
        )
        st.rerun()

    if expired and st.button("重新生成", key="regen_script_btn"):
        st.session_state.awaiting_script_confirmation = False
        st.session_state.pending_script = None
        # 用原用户请求触发一次重新生成
        st.session_state["auto_prompt"] = pending.get("user_request", "")
        st.rerun()


def _set_script_status(status: str) -> None:
    """把当前会话最新脚本消息状态写回存储（无会话绑定时静默跳过）。"""
    sid = st.session_state.get("current_session_id")
    if not isinstance(sid, str):
        return
    try:
        SessionStore().set_script_status(sid, status)
    except Exception as e:  # noqa: BLE001 - 状态落盘失败不阻断确认流程
        st.warning(f"脚本状态落盘失败：{e}")


def _init_session_state(store: SessionStore) -> None:
    """初始化会话状态；无当前会话时恢复最近会话或新建。"""
    defaults = {
        "messages": [],
        "fetch_candidates": [],
        "awaiting_confirmation": False,
        "awaiting_script_confirmation": False,
        "pending_script": None,
        "downloaded_assets": [],
        "chat_summary": None,
        "summary_upto": 0,
        "persisted_count": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if isinstance(st.session_state.get("current_session_id"), str):
        return  # 已绑定会话
    sessions = store.list_sessions()
    if sessions and not st.session_state["messages"]:
        _load_session(store, sessions[0]["id"])
    else:
        st.session_state["current_session_id"] = store.create_session()
        st.session_state["persisted_count"] = 0


def _reset_session_state() -> None:
    """新建/切换会话时清空消息与瞬态状态。"""
    st.session_state["messages"] = []
    st.session_state["persisted_count"] = 0
    st.session_state["chat_summary"] = None
    st.session_state["summary_upto"] = 0
    st.session_state["downloaded_assets"] = []
    st.session_state["fetch_candidates"] = []
    st.session_state["awaiting_confirmation"] = False
    st.session_state["awaiting_script_confirmation"] = False
    st.session_state["pending_script"] = None
    st.session_state["_scroll_pending"] = True


def _load_session(store: SessionStore, session_id: str) -> None:
    """加载会话：恢复消息、摘要、资产；最新 pending 脚本强制过期。"""
    session = store.get_session(session_id) or {}
    messages = store.get_messages(session_id)

    _reset_session_state()
    st.session_state["current_session_id"] = session_id
    st.session_state["messages"] = messages
    st.session_state["chat_summary"] = session.get("summary")
    st.session_state["summary_upto"] = session.get("summary_upto") or 0
    st.session_state["downloaded_assets"] = session.get("downloaded_assets") or []
    st.session_state["persisted_count"] = len(messages)

    # 仅看最新一条带 pending_script 的消息
    for msg in reversed(messages):
        ps = msg.get("pending_script")
        if not ps:
            continue
        if ps.get("status") in ("pending", "expired"):
            if ps["status"] == "pending":
                ps["status"] = "expired"
                store.set_script_status(session_id, "expired")
            st.session_state["pending_script"] = {
                "script": ps.get("script", ""),
                "analysis_type": ps.get("analysis_type"),
                "params": ps.get("params") or {},
                "method_context": ps.get("method_context"),
                "user_request": ps.get("user_request", ""),
                "status": "expired",
            }
            st.session_state["awaiting_script_confirmation"] = True
        break


def _persist_new_messages(store: SessionStore) -> None:
    """增量落盘新消息 + 更新会话元数据；失败仅告警不阻断。"""
    sid = st.session_state.get("current_session_id")
    if not isinstance(sid, str):
        return
    try:
        messages = st.session_state["messages"]
        start = st.session_state.get("persisted_count", 0)
        for msg in messages[start:]:
            store.append_message(
                sid,
                role=msg.get("role", "assistant"),
                content=str(msg.get("content") or ""),
                results=msg.get("results"),
                pending_script=msg.get("pending_script"),
            )
        st.session_state["persisted_count"] = len(messages)
        store.update_session_meta(
            sid,
            summary=st.session_state.get("chat_summary"),
            summary_upto=st.session_state.get("summary_upto") or 0,
            downloaded_assets=st.session_state.get("downloaded_assets") or [],
        )
    except Exception as e:  # noqa: BLE001 - 持久化失败不阻断对话
        st.warning(f"会话落盘失败：{e}")


def _render_session_sidebar(store: SessionStore) -> None:
    """侧边栏会话区：新建 / 切换 / 删除。"""
    st.header("会话")
    if st.button("＋ 新会话", key="new_session_btn"):
        st.session_state["current_session_id"] = store.create_session()
        _reset_session_state()
        st.rerun()

    for sess in store.list_sessions():
        cols = st.columns([4, 1])
        is_current = sess["id"] == st.session_state.get("current_session_id")
        label = f"{sess['title']}（{sess['message_count']}）"
        if cols[0].button(
            label, key=f"sess_{sess['id']}",
            type="primary" if is_current else "secondary",
        ):
            _load_session(store, sess["id"])
            st.rerun()
        if cols[1].button("🗑", key=f"del_{sess['id']}"):
            store.delete_session(sess["id"])
            if sess["id"] == st.session_state.get("current_session_id"):
                remaining = store.list_sessions()
                if remaining:
                    _load_session(store, remaining[0]["id"])
                else:
                    st.session_state["current_session_id"] = store.create_session()
                    _reset_session_state()
            st.rerun()


def _bootstrap():
    """streamlit run src/ui/app.py 入口：初始化 agent 并渲染界面"""
    from src.main import CellSpatioAgent

    create_app(CellSpatioAgent())


if __name__ == "__main__":
    _bootstrap()