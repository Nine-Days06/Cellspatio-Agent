"""AppTest：V1 会话恢复、V2 会话隔离与切换。"""

from streamlit.testing.v1 import AppTest

from src.ui.session_store import SessionStore

_ROOT = r"D:\Project\Multiomics-Agent"

_APP = """
import sys
from pathlib import Path
from unittest.mock import MagicMock

_root = Path(r"__ROOT__")
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.app import create_app

agent = MagicMock()
agent.knowledge_client.get_statistics.return_value = {"initialized": True}
create_app(agent)
""".replace("__ROOT__", _ROOT)


def _exception(at: AppTest) -> bool:
    return bool(at.exception)


def test_v1_restore_session_after_restart():
    """重启后恢复最近会话的消息、图表与会话绑定。"""
    store = SessionStore()
    sid = store.create_session("测试会话")
    store.append_message(sid, "user", "做差异表达")
    store.append_message(sid, "assistant", "分析完成: 第一次",
                         results={"data": [{"gene": "TP53"}]})

    at = AppTest.from_string(_APP, default_timeout=30)
    at.run()
    assert not _exception(at), f"App raised: {at.exception}"

    markdowns = [str(m.value) for m in at.markdown]
    assert any("做差异表达" in m for m in markdowns)
    assert any("分析完成: 第一次" in m for m in markdowns)
    # 图表数据恢复并渲染出基因追问下拉框
    assert "hist_1_gene_followup_select" in [s.key for s in at.selectbox]
    # 会话绑定
    assert at.session_state["current_session_id"] == sid


def test_v2_new_session_clears_and_isolates():
    """新建会话清空消息且不破坏原会话数据。"""
    store = SessionStore()
    sid1 = store.create_session("会话一")
    store.append_message(sid1, "user", "问题一")
    store.append_message(sid1, "assistant", "回答一")

    at = AppTest.from_string(_APP, default_timeout=30)
    at.run()
    assert not _exception(at), f"App raised: {at.exception}"
    assert at.session_state["current_session_id"] == sid1
    assert any("回答一" in str(m.value) for m in at.markdown)

    at.button("new_session_btn").click().run()
    assert not _exception(at), f"App raised: {at.exception}"
    new_sid = at.session_state["current_session_id"]
    assert new_sid != sid1
    assert at.session_state["messages"] == []
    assert not any("回答一" in str(m.value) for m in at.markdown)
    assert len(store.list_sessions()) == 2
    assert len(store.get_messages(sid1)) == 2   # 原会话数据未被破坏


def test_v2_switch_between_sessions():
    """点击侧边栏会话按钮切换，消息随之切换。"""
    store = SessionStore()
    sid1 = store.create_session("会话一")
    store.append_message(sid1, "user", "问题一")
    sid2 = store.create_session("会话二")
    store.append_message(sid2, "user", "问题二")

    at = AppTest.from_string(_APP, default_timeout=30)
    at.run()
    assert not _exception(at), f"App raised: {at.exception}"

    # 切换到另一个会话（按 key 定位）
    target = sid1 if at.session_state["current_session_id"] == sid2 else sid2
    at.button(f"sess_{target}").click().run()
    assert not _exception(at), f"App raised: {at.exception}"
    assert at.session_state["current_session_id"] == target
    contents = [str(m.value) for m in at.markdown]
    expected = "问题一" if target == sid1 else "问题二"
    assert any(expected in c for c in contents)


def test_bubble_css_and_markers_rendered():
    """CSS 与气泡 marker 应随应用注入。"""
    at = AppTest.from_string(_APP, default_timeout=30)
    at.run()
    assert not _exception(at), f"App raised: {at.exception}"
    all_md = "\n".join(str(m.value) for m in at.markdown)
    assert ":has(.cs-bubble-user)" in all_md
    assert ":has(.cs-bubble-assistant)" in all_md
    assert 'class="cs-bubble cs-bubble-user"' in all_md or "cs-bubble-user" in all_md


def test_render_scroll_flushes_on_flag(monkeypatch):
    """_scroll_pending 为真时应注入滚动脚本并清掉标记。"""
    from unittest.mock import patch

    from src.ui import app as app_mod

    with patch("src.ui.app.st") as mock_st:
        mock_st.session_state = {"_scroll_pending": True}
        app_mod._render_scroll()
    html_calls = [c for c in mock_st.html.call_args_list
                  if "scrollTo" in str(c)]
    assert html_calls, "应注入滚动脚本"
    assert "_scroll_pending" not in mock_st.session_state  # 已 pop


def test_render_scroll_noop_without_flag():
    from unittest.mock import patch

    from src.ui import app as app_mod

    with patch("src.ui.app.st") as mock_st:
        mock_st.session_state = {}
        app_mod._render_scroll()
    mock_st.html.assert_not_called()


def test_soft_threshold_warning_shown(monkeypatch):
    """超过软阈值时应用显示警告（经模块属性 patch 生效）。"""
    from src.control import compact as compact_mod

    monkeypatch.setattr(compact_mod, "SOFT_TRIGGER", 1)
    at = AppTest.from_string(_APP, default_timeout=30)
    at.run()
    assert not _exception(at), f"App raised: {at.exception}"
    warnings = [str(w.value) for w in at.warning]
    assert any("上下文已接近上限" in w for w in warnings)