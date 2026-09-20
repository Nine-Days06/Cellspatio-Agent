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
        stats = agent.knowledge_client.get_statistics()
        st.json(stats)
    
    # 主界面
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    # 显示聊天历史
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    # 用户输入
    if prompt := st.chat_input("请输入您的问题或分析需求"):
        # 显示用户消息
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # 执行工作流
        with st.chat_message("assistant"), st.spinner("思考中..."):
            result = agent.execute_workflow(prompt)
            
            # 根据结果类型显示不同内容
            if result.get('type') == 'knowledge_response':
                response = result.get('response', '无响应')
            elif result.get('results'):
                # 如果有分析结果，显示图表
                st.info(f"分析完成: {result.get('message', '')}")
                response = "分析结果已生成，请查看下方图表。"
            else:
                response = result.get('message', '处理完成')
            
            st.markdown(response)
            st.session_state.messages.append({"role": "assistant", "content": response})
