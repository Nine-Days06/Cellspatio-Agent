"""compact 纯函数单元测试：token 估算、历史裁剪、阈值判定。"""

from src.control import compact
from src.control.compact import (
    TRUNCATION_NOTE,
    context_tokens,
    estimate_tokens,
    prepare_history,
    select_recent,
    should_compact,
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
