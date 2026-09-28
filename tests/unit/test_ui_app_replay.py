"""create_app 历史重放集成测试（Streamlit AppTest，真实 widget key 校验）"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

# 项目根：供嵌入脚本设置 sys.path（tests/unit → 上两级）
_ROOT = Path(__file__).resolve().parents[2]

# 独立脚本：预置两条含分析结果的历史消息后运行真实 create_app，
# 若重放时 widget key 冲突，Streamlit 会抛 DuplicateElementId
_APP = '''
import sys
from pathlib import Path
from unittest.mock import MagicMock

_root = Path(r"__ROOT__")
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.ui.app import create_app

agent = MagicMock()
agent.knowledge_client.get_statistics.return_value = {"initialized": True}

# 预置两条含分析结果的历史消息，验证重放时 widget key 不冲突
import streamlit as st
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "user", "content": "做差异表达"},
        {"role": "assistant", "content": "分析完成: 第一次",
         "results": {"data": [{"gene": "TP53"}, {"gene": "BRCA1"}]}},
        {"role": "user", "content": "再做一次"},
        {"role": "assistant", "content": "分析完成: 第二次",
         "results": {"data": [{"gene": "EGFR"}, {"gene": "KRAS"}]}},
    ]
    st.session_state.fetch_candidates = []
    st.session_state.awaiting_confirmation = False
    st.session_state.awaiting_script_confirmation = False
    st.session_state.pending_script = None
    st.session_state.downloaded_assets = []

create_app(agent)
'''.replace("__ROOT__", str(_ROOT))


def test_replay_two_analysis_messages_no_duplicate_keys():
    """两条带 results 的历史消息重放不得触发 DuplicateElementId"""
    at = AppTest.from_string(_APP, default_timeout=30)
    at.run()
    assert not at.exception, f"App raised: {at.exception}"
    # 两个基因追问 selectbox 均渲染且 key 唯一
    keys = [s.key for s in at.selectbox]
    assert "hist_1_gene_followup_select" in keys
    assert "hist_3_gene_followup_select" in keys


def test_replay_renders_chart_data():
    """重放应渲染历史消息的分析数据表"""
    at = AppTest.from_string(_APP, default_timeout=30)
    at.run()
    assert not at.exception, f"App raised: {at.exception}"
    markdowns = [m.value for m in at.markdown]
    assert any("分析完成: 第一次" in str(m) for m in markdowns)
    assert any("分析完成: 第二次" in str(m) for m in markdowns)
