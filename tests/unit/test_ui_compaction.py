"""run_prompt 的上下文压缩接线测试。"""

from unittest.mock import MagicMock, patch

from src.control import compact


def _run(prompt="当前输入", prepared_len=3, summary=None, offset=0):
    mock_agent = MagicMock()
    mock_agent.execute_workflow.return_value = {
        "status": "success", "type": "general_response", "message": "ok",
    }
    mock_agent.llm_client = None
    mock_agent.llm_model = "fake-model"

    with patch("src.ui.app.st") as mock_st:
        mock_st.session_state = {
            "messages": [
                {"role": "user", "content": f"第{i}句" + "样例文本" * 5} for i in range(prepared_len)
            ],
            "downloaded_assets": [],
            "awaiting_confirmation": False,
            "awaiting_script_confirmation": False,
            "pending_script": None,
            "fetch_candidates": [],
            "fetch_query": "",
            "chat_summary": summary,
            "summary_upto": offset,
        }
        mock_st.chat_message.return_value.__enter__ = lambda s: None
        mock_st.chat_message.return_value.__exit__ = lambda s, *a: None
        mock_st.spinner.return_value.__enter__ = lambda s: None
        mock_st.spinner.return_value.__exit__ = lambda s, *a: None
        mock_st.rerun = lambda: None
        mock_st.markdown = lambda *a, **k: None

        from src.ui.app import run_prompt

        run_prompt(mock_agent, prompt)
    return mock_agent, mock_st


def test_run_prompt_passes_summary_in_context():
    """summary 存在时应透传到 context，供 _build_messages 注入 checkpoint。"""
    agent, _ = _run(summary="## 目标\n做差异", offset=2)
    context = agent.execute_workflow.call_args[1]["context"]
    assert context["summary"] == "## 目标\n做差异"


def test_run_prompt_without_summary_has_none():
    agent, _ = _run(summary=None, offset=0)
    context = agent.execute_workflow.call_args[1]["context"]
    assert context.get("summary") is None


def test_run_prompt_history_is_recent_window(monkeypatch):
    """压缩后 history 应是 select_recent 的 recent 窗口，而非全量。"""
    monkeypatch.setattr(compact, "TRIGGER", 1)          # 必然触发
    monkeypatch.setattr(compact, "RECENT_TOKEN_BUDGET", 10)  # 只留 2 条
    agent, _ = _run(prepared_len=6, summary=None, offset=0)
    context = agent.execute_workflow.call_args[1]["context"]
    # 至少保留 1 条，至多不超过原始长度
    assert 1 <= len(context["history"]) < 6