"""UI 消息结构化与渲染收敛回归测试"""

from unittest.mock import MagicMock, patch


def test_run_prompt_persists_results_into_message():
    """含分析结果的回复应把 results 存进消息，供 rerun 后重放"""
    from src.ui.app import run_prompt

    mock_agent = MagicMock()
    analysis_results = {"statistics": {"n": 1}, "data": [{"gene": "TP53"}], "charts": []}
    mock_agent.execute_workflow.return_value = {
        "status": "success",
        "type": "general_response",
        "message": "分析完成",
        "results": analysis_results,
    }

    with patch("src.ui.app.st") as mock_st:
        mock_st.session_state = {
            "messages": [],
            "downloaded_assets": [],
            "awaiting_confirmation": False,
            "awaiting_script_confirmation": False,
            "pending_script": None,
            "fetch_candidates": [],
            "fetch_query": "",
        }
        mock_st.chat_message.return_value.__enter__ = lambda s: None
        mock_st.chat_message.return_value.__exit__ = lambda s, *a: None
        mock_st.spinner.return_value.__enter__ = lambda s: None
        mock_st.spinner.return_value.__exit__ = lambda s, *a: None
        mock_st.rerun = lambda: None
        mock_st.markdown = lambda *a, **k: None
        mock_st.info = lambda *a, **k: None

        run_prompt(mock_agent, "做差异表达")

    assistant_msgs = [m for m in mock_st.session_state["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["results"] == analysis_results
    assert "分析完成" in assistant_msgs[0]["content"]


def test_run_prompt_plain_result_has_no_results_key():
    """普通回复消息不应携带 results 键（或为 None）"""
    from src.ui.app import run_prompt

    mock_agent = MagicMock()
    mock_agent.execute_workflow.return_value = {
        "status": "success",
        "type": "general_response",
        "message": "好的",
    }

    with patch("src.ui.app.st") as mock_st:
        mock_st.session_state = {
            "messages": [],
            "downloaded_assets": [],
            "awaiting_confirmation": False,
            "awaiting_script_confirmation": False,
            "pending_script": None,
            "fetch_candidates": [],
            "fetch_query": "",
        }
        mock_st.chat_message.return_value.__enter__ = lambda s: None
        mock_st.chat_message.return_value.__exit__ = lambda s, *a: None
        mock_st.spinner.return_value.__enter__ = lambda s: None
        mock_st.spinner.return_value.__exit__ = lambda s, *a: None
        mock_st.rerun = lambda: None
        mock_st.markdown = lambda *a, **k: None

        run_prompt(mock_agent, "你好")

    assistant_msgs = [m for m in mock_st.session_state["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert not assistant_msgs[0].get("results")


def test_render_analysis_results_accepts_key_prefix():
    """render_analysis_results 应接受 key_prefix 并用于 gene followup widget key"""
    from src.ui.components import render_analysis_results

    with patch("src.ui.components.st") as mock_st:
        mock_st.selectbox.return_value = "TP53"
        mock_st.button.return_value = False
        mock_st.markdown = lambda *a, **k: None
        mock_st.subheader = lambda *a, **k: None
        mock_st.json = lambda *a, **k: None
        mock_st.dataframe = lambda *a, **k: None
        mock_st.warning = lambda *a, **k: None

        render_analysis_results(
            {"data": [{"gene": "TP53"}]}, key_prefix="hist_0_"
        )

    _, kwargs = mock_st.selectbox.call_args
    assert kwargs["key"] == "hist_0_gene_followup_select"
    _, btn_kwargs = mock_st.button.call_args
    assert btn_kwargs["key"] == "hist_0_gene_followup_btn"
