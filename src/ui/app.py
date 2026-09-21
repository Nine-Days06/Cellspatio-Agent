from typing import Any

import streamlit as st


def create_app(agent: Any):
    """创建 Streamlit 应用"""
    
    st.title("人类多组学分析智能体")
    st.caption("交互式人类多组学数据分析与知识问答系统")
    
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
    
    # 显示聊天历史
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # 候选选择器（在聊天输入之前渲染，避免重复渲染问题）
    if st.session_state.get("awaiting_confirmation"):
        _render_candidate_selector(agent)
    
    # 用户输入
    if prompt := st.chat_input("请输入您的问题或分析需求"):
        # 显示用户消息
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # 执行工作流
        with st.chat_message("assistant"), st.spinner("思考中..."):
            result = agent.execute_workflow(prompt)
        
        if result.get('type') == 'fetch_data' and result.get('status') == 'needs_confirmation':
            st.session_state.fetch_candidates = result.get('candidates', [])
            st.session_state.awaiting_confirmation = True
            with st.chat_message("assistant"):
                st.markdown(result.get('message', '找到候选数据集，请选择要下载的项：'))
            st.rerun()
        else:
            _render_chat_result(result)
            st.session_state.messages.append({"role": "assistant", "content": _format_result(result)})


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
        if st.button("确认下载"):
            selected = label_map[choice]
            with st.spinner("下载中..."):
                result = agent.confirm_and_download(selected['source'], selected['asset_id'])
            st.session_state.awaiting_confirmation = False
            message = f"已下载 {selected['asset_id']} → `{result['asset']['access_path']}`"
            st.session_state.messages.append({"role": "assistant", "content": message})
            st.rerun()


def _render_chat_result(result: dict[str, Any]):
    """按结果类型渲染聊天回复"""
    if result.get('type') == 'knowledge_response':
        response = result.get('response', '无响应')
    elif result.get('type') == 'fetch_result':
        asset = result.get('asset', {})
        response = f"已下载 {asset.get('asset_id')} → `{asset.get('access_path')}`"
    elif result.get('results'):
        st.info(f"分析完成: {result.get('message', '')}")
        response = "分析结果已生成，请查看下方图表。"
    else:
        response = result.get('message', '处理完成')
    with st.chat_message("assistant"):
        st.markdown(response)


def _format_result(result: dict[str, Any]) -> str:
    """将结果格式化为聊天消息文本"""
    if result.get('type') == 'knowledge_response':
        return result.get('response', '无响应')
    elif result.get('type') == 'fetch_result':
        asset = result.get('asset', {})
        return f"已下载 {asset.get('asset_id')} → `{asset.get('access_path')}`"
    elif result.get('results'):
        return f"分析完成: {result.get('message', '')}"
    else:
        return result.get('message', '处理完成')