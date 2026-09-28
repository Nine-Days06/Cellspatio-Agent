"""compact 纯函数单元测试：token 估算、历史裁剪、阈值判定。"""

import pytest

from src.control import compact
from src.control.compact import (
    SUMMARY_PROMPT,
    TRUNCATION_NOTE,
    context_tokens,
    estimate_tokens,
    maybe_compact,
    prepare_history,
    select_recent,
    should_compact,
    summarize,
)


def test_estimate_tokens_is_len_div_four():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 1


def test_prepare_history_truncates_long_content_with_note():
    long = "x" * (compact.TOOL_OUTPUT_MAX_CHARS + 500)
    out = prepare_history([{"role": "user", "content": long}])
    assert len(out[0]["content"]) == compact.TOOL_OUTPUT_MAX_CHARS + len(TRUNCATION_NOTE)
    assert out[0]["content"].endswith(TRUNCATION_NOTE)


def test_prepare_history_returns_equal_copy_for_short_message():
    src = [{"role": "user", "content": "做差异表达"}]
    out = prepare_history(src)
    assert out == src          # 既有测试断言 history 内容等值
    assert out[0] is not src[0] # 但是副本，避免污染原对象


def test_select_recent_within_budget_keeps_all():
    window = [{"role": "user", "content": "abcd"}]
    older, recent = select_recent(window, budget=100)
    assert older == []
    assert recent == window


def test_select_recent_empty_window():
    assert select_recent([], budget=10) == ([], [])


def test_select_recent_keeps_at_least_one_even_over_budget():
    window = [{"role": "user", "content": "x" * 4000}]
    older, recent = select_recent(window, budget=10)
    assert older == []
    assert len(recent) == 1


def test_select_recent_splits_older_and_recent():
    window = [{"role": "user", "content": "x" * 40}] * 10  # 每条 10 token
    older, recent = select_recent(window, budget=35)
    assert len(older) >= 1
    assert len(recent) >= 1
    assert len(older) + len(recent) == 10
    assert recent == window[len(older):]  # recent 是尾部


def test_context_tokens_includes_system_summary_and_window():
    summary = "s" * 40          # 10 token
    window = [{"role": "user", "content": "h" * 40}]  # 10 token
    assert context_tokens(summary, window, system="") == 20


def test_should_compact_respects_trigger(monkeypatch):
    monkeypatch.setattr(compact, "TRIGGER", 5)
    assert should_compact("s" * 40, [], system="") is True   # 10 > 5
    assert should_compact("", [], system="") is False


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.content)


class FakeLLM:
    def __init__(self, content="摘要内容"):
        self.completions = FakeCompletions(content)
        self.chat = type("Chat", (), {"completions": self.completions})()


def test_summarize_builds_prompt_and_returns_text():
    llm = FakeLLM("## 目标\n做差异")
    out = summarize(llm, "m1", None, [{"role": "user", "content": "问题"}])
    assert out == "## 目标\n做差异"
    call = llm.completions.calls[0]
    assert call["model"] == "m1"
    assert call["temperature"] == 0
    assert call["max_tokens"] == 2048
    assert call["messages"][0]["content"] == SUMMARY_PROMPT
    assert "（无）" in call["messages"][1]["content"]
    assert "[user] 问题" in call["messages"][1]["content"]


def test_summarize_includes_prior_summary():
    llm = FakeLLM("新摘要")
    summarize(llm, "m1", "旧摘要", [{"role": "user", "content": "x"}])
    assert "旧摘要" in llm.completions.calls[0]["messages"][1]["content"]


def test_summarize_empty_result_raises():
    with pytest.raises(ValueError):
        summarize(FakeLLM("   "), "m1", None, [{"role": "user", "content": "x"}])


def test_maybe_compact_below_trigger_is_noop(monkeypatch):
    monkeypatch.setattr(compact, "TRIGGER", 10_000)
    called = []
    out = maybe_compact(None, 0, [{"role": "user", "content": "hi"}],
                        lambda *a: called.append(a), system="")
    assert out == (None, 0)
    assert called == []


def test_maybe_compact_llm_none_skips(monkeypatch):
    monkeypatch.setattr(compact, "TRIGGER", 1)
    prepared = [{"role": "user", "content": "x" * 40}]
    assert maybe_compact(None, 0, prepared, None, system="") == (None, 0)


def test_maybe_compact_success_advances_offset(monkeypatch):
    monkeypatch.setattr(compact, "TRIGGER", 1)
    monkeypatch.setattr(compact, "RECENT_TOKEN_BUDGET", 20)  # 只留最近 2 条
    prepared = [{"role": "user", "content": "x" * 40} for _ in range(6)]

    def fake(prior, older):
        assert prior is None
        assert older  # 确实有被摘要掉的旧消息
        return "## 目标\n新摘要"

    summary, offset = maybe_compact(None, 0, prepared, fake, system="")
    assert summary == "## 目标\n新摘要"
    assert 0 < offset < 6  # 消化了部分，仍保留近期消息


def test_maybe_compact_exception_falls_back_to_hard_trim(monkeypatch):
    monkeypatch.setattr(compact, "TRIGGER", 1)
    monkeypatch.setattr(compact, "RECENT_TOKEN_BUDGET", 20)
    prepared = [{"role": "user", "content": "x" * 40} for _ in range(6)]

    def boom(prior, older):
        raise RuntimeError("LLM down")

    summary, offset = maybe_compact(None, 0, prepared, boom, system="")
    assert summary is None                      # 摘要未变
    assert offset > 0                           # 硬截断已推进
    # 截断后不再超阈值，或已保留最后 1 条
    assert (not should_compact(summary, prepared[offset:], system="")) \
        or offset == len(prepared) - 1


def test_maybe_compact_empty_llm_result_falls_back(monkeypatch):
    monkeypatch.setattr(compact, "TRIGGER", 1)
    monkeypatch.setattr(compact, "RECENT_TOKEN_BUDGET", 20)
    prepared = [{"role": "user", "content": "x" * 40} for _ in range(6)]
    summary, offset = maybe_compact(None, 0, prepared, lambda p, o: "", system="")
    assert summary is None
    assert offset > 0


def test_should_soft_warn_below_soft_trigger(monkeypatch):
    from src.control.compact import should_soft_warn

    monkeypatch.setattr(compact, "SOFT_TRIGGER", 5)
    assert should_soft_warn("s" * 40, [], system="") is True   # 10 > 5
    assert should_soft_warn("", [], system="") is False
