# tests/unit/test_eval_harness.py
"""金标用例结构与运行器 scripted 模式。"""

import json
from pathlib import Path

CASES = Path(__file__).resolve().parents[2] / "evals" / "cases.jsonl"


def _load_cases():
    lines = CASES.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_cases_file_has_required_fields_and_known_tools(monkeypatch):
    """金标用例字段完整，且期望工具都在默认工具清单里。"""
    from src.control.tools import build_tool_schemas

    monkeypatch.delenv("AGENT_KNOWLEDGE_TOOL", raising=False)
    names = {t["function"]["name"] for t in build_tool_schemas()} | {None}
    cases = _load_cases()
    assert len(cases) >= 8
    ids = set()
    for case in cases:
        assert case["id"] not in ids
        ids.add(case["id"])
        assert case["input"]
        # 支持两种格式：原有工具路由格式和 P3d 扩展格式
        if "expect_tool" in case:
            assert case["expect_tool"] in names
            if case["expect_tool"] == "run_analysis":
                assert case["expect_args"]["analysis_type"] in (
                    "differential_expression",
                    "single_cell",
                    "spatial",
                )


def test_run_eval_scripted_mode_scores_perfect_on_oracle(monkeypatch):
    """scripted：FakeLLM 直接回放金标期望工具 → 应 100%（校验运行器本身）。"""
    from evals.run_eval import run_scripted

    monkeypatch.delenv("AGENT_KNOWLEDGE_TOOL", raising=False)
    report = run_scripted(str(CASES))
    assert report["total"] == report["passed"]
    assert report["accuracy"] == 1.0
    assert report["failures"] == []


def test_every_case_is_covered_by_p3d_categories():
    """p3d 汇总只统计已分类用例，缺分类的用例会被静默丢弃、准确率假绿。"""
    categories = (
        "expect_skill",
        "expect_kg_write",
        "expect_kg_read",
        "expect_kg_query",
        "expect_best_practice",
        "expect_tool",
    )
    uncovered = [c["id"] for c in _load_cases() if not any(k in c for k in categories)]
    assert uncovered == []
