import sys
from pathlib import Path
from typing import Any

import streamlit as st

# 保证以 `streamlit run src/ui/app.py` 启动时，模块级导入也能 `import src.*`
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.components import render_analysis_results, render_starter_presets


def run_prompt(agent: Any, prompt: str) -> None:
    """统一执行：显示用户消息 → workflow → 渲染/确认流 → 入历史"""
    import streamlit as st

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"), st.spinner("思考中..."):
        result = agent.execute_workflow(prompt)

    if result.get("status") == "needs_script_confirmation":
        st.session_state.pending_script = {
            "script": result.get("script", ""),
            "analysis_type": result.get("analysis_type"),
            "params": result.get("params") or {},
            "method_context": result.get("method_context"),
        }
        st.session_state.awaiting_script_confirmation = True
        with st.chat_message("assistant"):
            st.markdown(result.get("message", "已生成 R 脚本，请审阅并确认执行"))
            _render_script_confirmation(agent)
        st.session_state.messages.append(
            {"role": "assistant", "content": _format_script_confirmation(result)}
        )
        return

    if result.get("type") == "fetch_data" and result.get("status") == "needs_confirmation":
        st.session_state.fetch_candidates = result.get("candidates", [])
        st.session_state.fetch_query = result.get("query", "")
        st.session_state.awaiting_confirmation = True
        with st.chat_message("assistant"):
            st.markdown(result.get("message", "找到候选数据集，请选择要下载的项："))
        st.rerun()
        return

    _render_chat_result(result)
    st.session_state.messages.append(
        {"role": "assistant", "content": _format_result(result)}
    )


def create_app(agent: Any):
    """创建 Streamlit 应用"""
    
    st.title("CellSpatio 单细胞与时空组学分析智能体")
    st.caption("单细胞与空间/时序组学分析 · LightRAG 知识问答")
    
    # 侧边栏配置
    with st.sidebar:
        st.header("设置")
        external_api = st.checkbox("启用外部 API 查询", value=False)
        if external_api:
            st.info("外部 API 已启用，将查询最新文献和数据库。")
        
        st.header("知识库状态")
        try:
            stats = agent.knowledge_client.get_statistics()
            st.json(stats)
        except Exception as e:
            st.error(f"知识库状态获取失败: {e}")
            st.json({"working_dir": "knowledge_base", "initialized": False})
    
    # 初始化会话状态
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "fetch_candidates" not in st.session_state:
        st.session_state.fetch_candidates = []
    if "awaiting_confirmation" not in st.session_state:
        st.session_state.awaiting_confirmation = False
    if "awaiting_script_confirmation" not in st.session_state:
        st.session_state.awaiting_script_confirmation = False
    if "pending_script" not in st.session_state:
        st.session_state.pending_script = None
    
    # 显示聊天历史
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
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


def _render_candidate_selector(agent: Any):
    """渲染候选数据集选择器与确认下载按钮"""
    with st.chat_message("assistant"):
        candidates = st.session_state.fetch_candidates or []
        if not candidates:
            st.session_state.awaiting_confirmation = False
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
            import pandas as pd

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
            st.session_state.awaiting_confirmation = False
            message = f"已下载 {selected['asset_id']} → `{result['asset']['access_path']}`"
            st.session_state.messages.append({"role": "assistant", "content": message})
            st.rerun()


def _render_references(references: list[dict[str, Any]]) -> None:
    """结构化引用单独渲染（与正文分离，避免与 LLM 自造段混淆）"""
    if not references:
        return
    from src.knowledge.lightrag_client import format_reference

    st.markdown("**来源**")
    for ref in references:
        st.markdown(f"- {format_reference(ref)}")


def _render_chat_result(result: dict[str, Any]):
    """按结果类型渲染聊天回复"""
    references: list[dict[str, Any]] = []
    if result.get('type') == 'knowledge_response':
        response = result.get('response', '无响应')
        references = list(result.get('references') or [])
    elif result.get('type') == 'fetch_result':
        asset = result.get('asset', {})
        response = f"已下载 {asset.get('asset_id')} → `{asset.get('access_path')}`"
    elif result.get('results'):
        st.info(f"分析完成: {result.get('message', '')}")
        render_analysis_results(result["results"])
        response = "分析结果已生成，请查看下方图表。"
        if result.get('capsule_dir'):
            response += f"\n\n复现胶囊：`{result['capsule_dir']}`"
    else:
        response = result.get('message', '处理完成')
    explanation = result.get('explanation')
    if explanation:
        response = f"{response}\n\n---\n**结果解读**\n\n{explanation}"
    with st.chat_message("assistant"):
        st.markdown(response)
        _render_references(references)


def _format_result(result: dict[str, Any]) -> str:
    """将结果格式化为聊天消息文本"""
    references: list[dict[str, Any]] = []
    if result.get('type') == 'knowledge_response':
        response = result.get('response', '无响应')
        references = list(result.get('references') or [])
    elif result.get('type') == 'fetch_result':
        asset = result.get('asset', {})
        response = f"已下载 {asset.get('asset_id')} → `{asset.get('access_path')}`"
    elif result.get('results'):
        response = f"分析完成: {result.get('message', '')}"
        if result.get('capsule_dir'):
            response += f"\n复现胶囊：`{result['capsule_dir']}`"
    else:
        response = result.get('message', '处理完成')
    explanation = result.get('explanation')
    if explanation:
        response = f"{response}\n\n---\n**结果解读**\n\n{explanation}"
    if references:
        from src.knowledge.lightrag_client import format_reference

        lines = "\n".join(f"- {format_reference(r)}" for r in references)
        response = f"{response}\n\n**来源**\n{lines}"
    return response


def _format_script_confirmation(result: dict[str, Any]) -> str:
    """将脚本确认结果格式化为聊天消息文本"""
    return (
        f"{result.get('message', '已生成 R 脚本，请审阅并确认执行')}\n\n"
        f"```r\n{result.get('script', '')}\n```"
    )


def _render_script_confirmation(agent: Any) -> None:
    """渲染脚本确认 UI"""
    pending = st.session_state.pending_script
    if not pending:
        st.session_state.awaiting_script_confirmation = False
        return
    st.markdown("**待执行脚本（请审阅）**")
    st.code(pending["script"], language="r")
    col1, col2 = st.columns(2)
    if col1.button("确认执行", type="primary", key="confirm_script_btn"):
        with st.spinner("执行中..."):
            result = agent.execute_confirmed_script(
                pending["analysis_type"],
                pending["params"],
                pending["script"],
                method_context=pending.get("method_context"),
            )
        st.session_state.awaiting_script_confirmation = False
        st.session_state.pending_script = None
        _render_chat_result(result)
        st.session_state.messages.append(
            {"role": "assistant", "content": _format_result(result)}
        )
        st.rerun()
    if col2.button("取消", key="cancel_script_btn"):
        st.session_state.awaiting_script_confirmation = False
        st.session_state.pending_script = None
        st.session_state.messages.append(
            {"role": "assistant", "content": "已取消本次脚本执行。"}
        )
        st.rerun()


def _bootstrap():
    """streamlit run src/ui/app.py 入口：初始化 agent 并渲染界面"""
    import sys
    from pathlib import Path

    # 保证以 `streamlit run src/ui/app.py` 启动时能 `import src.*`
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from src.main import CellSpatioAgent

    create_app(CellSpatioAgent())


if __name__ == "__main__":
    _bootstrap()
