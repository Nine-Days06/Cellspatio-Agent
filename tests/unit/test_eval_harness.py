# tests/unit/test_eval_harness.py
"""金标用例结构与运行器 scripted 模式。"""
import json
from pathlib import Path

CASES = Path(__file__).resolve().parents[2] / "evals" / "cases.jsonl"


def _load_cases():
    lines = CASES.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_cases_file_has_required_fields_and_known_tools():
    from src.control.tools import TOOL_SCHEMAS

    names = {t["function"]["name"] for t in TOOL_SCHEMAS} | {None}
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


def test_run_eval_scripted_mode_scores_perfect_on_oracle():
    """scripted：FakeLLM 直接回放金标期望工具 → 应 100%（校验运行器本身）。"""
    from evals.run_eval import run_scripted

    report = run_scripted(str(CASES))
    assert report["total"] == report["passed"]
    assert report["accuracy"] == 1.0
    assert report["failures"] == []