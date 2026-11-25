"""混合文献链路评测用例与运行器。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evals.run_eval import load_cases
from evals.run_knowledge_tool_eval import (
    DEFAULT_CASES,
    _compare_baseline,
    _run_case,
    _summary,
)


@pytest.fixture(autouse=True)
def _knowledge_tool_on(monkeypatch):
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")


def test_cases_cover_required_mix_and_scripts_end_with_final():
    """评测集必须覆盖路线图五类场景，且每个脚本以最终回答收尾。"""
    cases = {case["id"]: case for case in load_cases(DEFAULT_CASES)}
    categories = {case["category"] for case in cases.values()}

    assert {
        "literature",
        "no_tool",
        "analysis",
        "analysis_then_literature",
        "literature_then_memory",
        "followup",
        "strict_literature",
        "service_error",
    } <= categories
    for case in cases.values():
        script = case["script"]
        assert script, case["id"]
        assert "final" in script[-1], case["id"]


def test_scripted_run_scores_oracle_cases():
    records = [_run_case(case) for case in load_cases(DEFAULT_CASES)]
    summary = _summary(records, 0.0, 0.0)

    assert summary["tool_selection_accuracy"] == 1.0
    assert summary["citation_validity"] == 1.0
    assert summary["answer_match"] == 1.0
    assert summary["input_tokens"] > 0


def test_scripted_run_is_independent_of_process_flag(monkeypatch):
    """即使进程显式回退 legacy，评测也为用例固定开启检索工具。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "legacy")
    records = [_run_case(case) for case in load_cases(DEFAULT_CASES)]

    assert all(record["tool_selection_ok"] for record in records)
    assert os.environ["AGENT_KNOWLEDGE_TOOL"] == "legacy"


def test_baseline_comparison_flags_regression():
    summary = {"answer_match": 0.7, "citation_validity": 0.9}
    comparison = _compare_baseline(
        summary, {"summary": {"answer_match": 0.8, "citation_validity": 0.9}}
    )

    assert comparison["answer_match"]["regressed"] is True
    assert comparison["citation_validity"]["regressed"] is False


def test_baseline_missing_metric_is_skipped():
    comparison = _compare_baseline({"answer_match": 1.0}, {"summary": {}})

    assert comparison["answer_match"] is None


def test_report_contains_no_absolute_paths_or_private_fields():
    records = [_run_case(case) for case in load_cases(DEFAULT_CASES)]
    payload = json.dumps(records, ensure_ascii=False)

    assert "file_path" not in payload
    assert str(Path.cwd()) not in payload
