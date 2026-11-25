"""Phase 0 模式使用基线统计。"""

from __future__ import annotations

from evals.measure_chat_modes import measure
from src.ui.session_store import SessionStore


def test_measure_counts_switches_and_followup_analysis(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session()
    store.append_message(sid, "user", "查文献")
    store.append_message(
        sid,
        "assistant",
        "文献回答 [E1]",
        results={"chat_mode": "knowledge_qa", "knowledge_evidence": {"citations": ["E1"]}},
    )
    store.append_message(sid, "user", "继续分析")
    store.append_message(
        sid,
        "assistant",
        "分析完成",
        results={"chat_mode": "chat", "charts": [{"type": "image"}]},
    )
    store.append_message(sid, "user", "再查一次")
    store.append_message(
        sid,
        "assistant",
        "旧问答",
        results={"knowledge_qa": {"status": "success"}},
    )
    store.append_message(sid, "assistant", "（已中断）")

    report = measure(store)

    assert report["assistant_turns"] == {"chat": 1, "knowledge_qa": 2}
    assert report["sessions_with_mode_switch"] == 1
    assert report["knowledge_to_chat_switches"] == 1
    assert report["knowledge_then_analysis"] == 1
