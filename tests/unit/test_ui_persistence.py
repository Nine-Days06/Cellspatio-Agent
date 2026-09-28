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


def test_run_prompt_script_branch_only_sets_state_no_render():
    """needs_script_confirmation 分支应只设状态+落历史，不调用 _render_script_confirmation"""
    from src.ui import app as app_mod
    from src.ui.app import run_prompt

    mock_agent = MagicMock()
    mock_agent.execute_workflow.return_value = {
        "status": "needs_script_confirmation",
        "script": "plot(1)",
        "analysis_type": "de",
        "params": {},
        "message": "请审阅脚本",
    }

    with patch("src.ui.app.st") as mock_st, \
         patch.object(app_mod, "_render_script_confirmation") as mock_render:
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

        run_prompt(mock_agent, "做差异表达")

    mock_render.assert_not_called()
    assert mock_st.session_state["awaiting_script_confirmation"] is True
    assert any("请审阅脚本" in m["content"] for m in mock_st.session_state["messages"])


def test_run_prompt_fetch_branch_persists_prompt_message():
    """fetch needs_confirmation 分支的提示语必须入历史，rerun 后不丢失"""
    from src.ui.app import run_prompt

    mock_agent = MagicMock()
    mock_agent.execute_workflow.return_value = {
        "status": "needs_confirmation",
        "type": "fetch_data",
        "candidates": [{"source": "GEO", "asset_id": "GSE1", "title": "t", "reason": "r"}],
        "query": "肝癌 RNA-seq",
        "message": "找到候选数据集，请选择要下载的项：",
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

        run_prompt(mock_agent, "下载数据")

    msgs = mock_st.session_state["messages"]
    assert any("找到候选数据集" in m["content"] for m in msgs)
    assert mock_st.session_state["awaiting_confirmation"] is True
    assert mock_st.session_state["fetch_candidates"] != []


def test_render_script_confirmation_renders_buttons_not_script():
    """确认组件只渲染按钮，不重复渲染脚本（脚本由历史消息承载）"""
    from src.ui.app import _render_script_confirmation

    mock_agent = MagicMock()
    mock_agent.execute_confirmed_script.return_value = {
        "status": "success",
        "message": "完成",
    }

    with patch("src.ui.app.st") as mock_st:
        pending = {"script": "plot(1)", "analysis_type": "de", "params": {}}
        # 代码按属性访问 session_state（.pending_script），dict 会 AttributeError，故用 MagicMock
        mock_st.session_state = MagicMock()
        mock_st.session_state.pending_script = pending
        mock_st.session_state.awaiting_script_confirmation = True
        col1, col2 = MagicMock(), MagicMock()
        col1.button.return_value = False
        col2.button.return_value = False
        mock_st.columns.return_value = (col1, col2)
        mock_st.chat_message.return_value.__enter__ = lambda s: None
        mock_st.chat_message.return_value.__exit__ = lambda s, *a: None

        _render_script_confirmation(mock_agent)

    mock_st.code.assert_not_called()
    assert col1.button.called
    assert col2.button.called


def test_render_analysis_results_renders_image_b64_chart():
    """持久化的 matplotlib 图表（image_b64 dict）应被解码渲染。"""
    import base64
    from unittest.mock import patch

    from src.ui.components import render_analysis_results

    png = base64.b64encode(b"\x89PNG-fakebytes").decode()
    with patch("src.ui.components.st") as mock_st:
        render_analysis_results({"charts": [{"type": "image", "image_b64": png}]})
    mock_st.image.assert_called_once()
    assert mock_st.image.call_args[0][0] == b"\x89PNG-fakebytes"
    mock_st.warning.assert_not_called()  # 不应落入"未知图表类型"
