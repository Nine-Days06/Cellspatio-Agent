"""首轮自动命名：只验证外部可观察结果（标题、来源、是否调用模型）。"""

from __future__ import annotations

import threading

import pytest

from src.ui import session_title
from src.ui.session_store import SessionStore
from src.ui.session_title import clean_title, maybe_name_session


class _FakeCompletions:
    def __init__(self, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        message = type("Message", (), {"content": self.content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class FakeLLM:
    def __init__(self, content: str | None = "PBMC 质控与聚类", error: Exception | None = None):
        self.completions = _FakeCompletions(content, error)
        self.chat = type("Chat", (), {"completions": self.completions})()


def _first_turn(store: SessionStore, title: str | None = None) -> str:
    sid = store.create_session(title)
    store.append_message(sid, "user", "帮我看看这份 PBMC 数据，先做质控再做聚类")
    store.append_message(sid, "assistant", "已完成质控，接下来可以做 PCA 与聚类。")
    return sid


def test_clean_title_strips_wrapping_and_punctuation():
    assert clean_title("  “PBMC 质控 与\n聚类。”  ") == "PBMC 质控 与 聚类"
    assert clean_title("《肝癌差异表达分析》") == "肝癌差异表达分析"
    assert clean_title("标题：PBMC 聚类") == "PBMC 聚类"
    assert clean_title("`TP53 通路分析`.") == "TP53 通路分析"


def test_clean_title_truncates_to_limit():
    assert len(clean_title("长" * 60)) == session_title.TITLE_MAX_CHARS


def test_first_turn_title_written_with_model_source():
    store = SessionStore()
    sid = _first_turn(store)
    llm = FakeLLM('"PBMC 质控与聚类"')

    assert maybe_name_session(store, sid, llm, "m1") == "PBMC 质控与聚类"
    session = store.get_session(sid)
    assert (session["title"], session["title_source"]) == ("PBMC 质控与聚类", "model")
    call = llm.completions.calls[0]
    assert call["temperature"] == 0
    assert call["max_tokens"] == 64


def test_inputs_are_truncated_before_sending():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "user", "问" * 5_000)
    store.append_message(sid, "assistant", "答" * 5_000)
    llm = FakeLLM("短标题")

    maybe_name_session(store, sid, llm, "m1")

    payload = llm.completions.calls[0]["messages"][1]["content"]
    assert payload.count("问") == session_title.INPUT_MAX_CHARS
    assert payload.count("答") == session_title.INPUT_MAX_CHARS


@pytest.mark.parametrize("content", ["", "   \n  ", "''"])
def test_empty_or_blank_result_keeps_fallback_title(content):
    store = SessionStore()
    sid = _first_turn(store)
    before = store.get_session(sid)

    assert maybe_name_session(store, sid, FakeLLM(content), "m1") is None
    after = store.get_session(sid)
    assert (after["title"], after["title_source"]) == (before["title"], before["title_source"])


def test_model_error_keeps_fallback_title_and_does_not_raise():
    store = SessionStore()
    sid = _first_turn(store)
    before = store.get_session(sid)["title"]

    assert maybe_name_session(store, sid, FakeLLM(error=RuntimeError("boom")), "m1") is None
    assert store.get_session(sid)["title"] == before


def test_timeout_keeps_fallback_title(monkeypatch):
    store = SessionStore()
    sid = _first_turn(store)
    before = store.get_session(sid)["title"]
    release = threading.Event()

    class SlowLLM:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": self})()

        def create(self, **kwargs):
            release.wait(5)
            return FakeLLM("迟到的标题").completions.create()

    monkeypatch.setattr(session_title, "TIMEOUT_SECONDS", 0.05)
    try:
        assert maybe_name_session(store, sid, SlowLLM(), "m1") is None
        assert store.get_session(sid)["title"] == before
    finally:
        release.set()


def test_skips_when_source_is_explicit_or_model():
    store = SessionStore()
    explicit = _first_turn(store, "上传 10x 数据")
    llm = FakeLLM("不该出现")
    assert maybe_name_session(store, explicit, llm, "m1") is None

    store2 = SessionStore()
    sid = _first_turn(store2)
    store2.set_session_title(sid, "已命名", source="model")
    assert maybe_name_session(store2, sid, llm, "m1") is None
    assert llm.completions.calls == []


def test_skips_when_not_first_turn_or_no_llm():
    store = SessionStore()
    sid = _first_turn(store)
    store.append_message(sid, "user", "第二问")
    store.append_message(sid, "assistant", "第二答")
    llm = FakeLLM("不该出现")

    assert maybe_name_session(store, sid, llm, "m1") is None
    assert maybe_name_session(store, sid, None, "m1") is None
    assert llm.completions.calls == []


def test_skips_when_only_one_message():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "user", "只有提问")
    llm = FakeLLM("不该出现")
    assert maybe_name_session(store, sid, llm, "m1") is None
    assert llm.completions.calls == []
