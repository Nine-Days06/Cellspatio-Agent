"""Test UI context passing: history & downloaded_assets"""

from unittest.mock import MagicMock, patch


def test_run_prompt_passes_history_to_agent(monkeypatch):
    """run_prompt 应将 st.session_state.messages 作为 history 传给 agent"""
    from src.ui.app import run_prompt

    mock_agent = MagicMock()
    mock_agent.execute_workflow.return_value = {
        "status": "success",
        "type": "general_response",
        "message": "ok",
    }

    with patch("src.ui.app.st") as mock_st:
        mock_st.session_state = {
            "messages": [
                {"role": "user", "content": "第一句"},
                {"role": "assistant", "content": "回复1"},
                {"role": "user", "content": "第二句"},
            ],
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

        run_prompt(mock_agent, "当前输入")

    call_args = mock_agent.execute_workflow.call_args
    assert call_args is not None
    context = call_args[1].get("context") or call_args[0][1]
    assert context is not None
    assert "history" in context
    assert len(context["history"]) == 3
    assert context["history"][0]["content"] == "第一句"
    assert context["history"][-1]["content"] == "第二句"
    assert context["last_user_input"] == "当前输入"


def test_run_prompt_passes_downloaded_assets_to_agent(monkeypatch):
    """run_prompt 应将 st.session_state.downloaded_assets 传给 agent"""
    from src.ui.app import run_prompt

    mock_agent = MagicMock()
    mock_agent.execute_workflow.return_value = {
        "status": "success",
        "type": "general_response",
        "message": "ok",
    }

    with patch("src.ui.app.st") as mock_st:
        mock_st.session_state = {
            "messages": [{"role": "user", "content": "hi"}],
            "downloaded_assets": [
                {"asset_id": "GSE123", "access_path": "/path/to/GSE123.txt", "source": "GEO"},
                {"asset_id": "GSE456", "access_path": "/path/to/GSE456.txt", "source": "GEO"},
            ],
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

    call_args = mock_agent.execute_workflow.call_args
    context = call_args[1].get("context") or call_args[0][1]
    assert "downloaded_assets" in context
    assert len(context["downloaded_assets"]) == 2
    assert context["downloaded_assets"][0]["asset_id"] == "GSE123"
    assert context["downloaded_assets"][1]["asset_id"] == "GSE456"


def test_candidate_selector_persists_downloaded_asset(monkeypatch):
    """_render_candidate_selector 确认下载后应将 asset 追加到 session_state.downloaded_assets"""
    from src.ui.app import _render_candidate_selector

    mock_agent = MagicMock()
    mock_agent.confirm_and_download.return_value = {
        "asset": {"asset_id": "GSE123", "access_path": "/path/to/GSE123.txt", "source": "GEO"}
    }

    with patch("src.ui.app.st") as mock_st:
        mock_st.selectbox.return_value = "[GEO] GSE123 - Test Dataset"
        mock_st.button.return_value = True
        mock_st.session_state = {
            "fetch_candidates": [
                {"source": "GEO", "asset_id": "GSE123", "title": "Test Dataset", "reason": "匹配", "metadata": {}}
            ],
            "fetch_query": "test query",
            "awaiting_confirmation": True,
            "downloaded_assets": [{"asset_id": "OLD", "access_path": "/old.txt"}],
            "messages": [],
        }
        mock_st.chat_message.return_value.__enter__ = lambda s: None
        mock_st.chat_message.return_value.__exit__ = lambda s, *a: None
        mock_st.caption = lambda *a, **k: None
        mock_st.info = lambda *a, **k: None
        mock_st.markdown = lambda *a, **k: None
        mock_st.dataframe = lambda *a, **k: None
        mock_st.spinner.return_value.__enter__ = lambda s: None
        mock_st.spinner.return_value.__exit__ = lambda s, *a: None
        mock_st.rerun = lambda: None

        _render_candidate_selector(mock_agent)

    assets = mock_st.session_state["downloaded_assets"]
    assert len(assets) == 2
    assert assets[0]["asset_id"] == "OLD"
    assert assets[1]["asset_id"] == "GSE123"
    assert assets[1]["access_path"] == "/path/to/GSE123.txt"
