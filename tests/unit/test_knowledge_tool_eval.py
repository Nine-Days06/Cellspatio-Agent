"""混合文献链路评测用例与三层分流。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evals import run_knowledge_tool_eval as ev
from evals.run_eval import load_cases
from src.control.workflow_manager import WorkflowManager


@pytest.fixture(autouse=True)
def _knowledge_tool_on(monkeypatch):
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "search")


def _record(**overrides):
    """最小指标记录，覆盖摘要需要的字段。"""
    base = {
        "id": "case-1",
        "category": "literature",
        "expected_tools": ["search_knowledge"],
        "actual_tools": ["search_knowledge"],
        "tool_selection_ok": True,
        "expect_citations": True,
        "citation_ok": True,
        "answer": "答 [E1]",
        "answer_match": True,
        "latency_ms": 10,
        "input_tokens": None,
        "output_tokens": None,
        "outcome": "supported_answer",
        "status": "success",
    }
    base.update(overrides)
    return base


def test_cases_cover_required_mix_and_scripts_end_with_final():
    """评测集必须覆盖路线图五类场景，且每个脚本以最终回答收尾。"""
    cases = {case["id"]: case for case in load_cases(ev.DEFAULT_CASES)}
    categories = {case["category"] for case in cases.values()}

    assert {
        "literature",
        "no_tool",
        "analysis",
        "analysis_then_literature",
        "literature_then_memory",
        "followup",
        "service_error",
    } <= categories
    for case in cases.values():
        script = case["script"]
        assert script, case["id"]
        assert "final" in script[-1], case["id"]


def test_scripted_run_scores_oracle_cases():
    """脚本层实跑应命中全部预置期望。"""
    records = [ev._run_scripted_case(case) for case in load_cases(ev.DEFAULT_CASES)]
    summary = ev._summary(records, 0.0, 0.0, ev.MODE_SCRIPTED)

    assert summary["tool_selection_accuracy"] == 1.0
    assert summary["citation_validity"] == 1.0
    assert summary["answer_match"] == 1.0
    assert summary["input_tokens"] > 0


def test_scripted_run_is_independent_of_process_flag(monkeypatch):
    """即使进程显式回退 legacy，评测也为用例固定开启检索工具。"""
    monkeypatch.setenv("AGENT_KNOWLEDGE_TOOL", "legacy")
    records = [ev._run_scripted_case(case) for case in load_cases(ev.DEFAULT_CASES)]

    assert all(record["tool_selection_ok"] for record in records)
    assert os.environ["AGENT_KNOWLEDGE_TOOL"] == "legacy"


def test_baseline_comparison_flags_regression():
    """基线容差 2 个百分点：答案命中下降报告回归。"""
    summary = {"answer_match": 0.7, "citation_validity": 0.9}
    comparison = ev._compare_baseline(
        summary, {"summary": {"answer_match": 0.8, "citation_validity": 0.9}}
    )

    assert comparison["answer_match"]["regressed"] is True
    assert comparison["citation_validity"]["regressed"] is False


def test_baseline_missing_metric_is_skipped():
    """基线缺字段时明确跳过，不臆造回归。"""
    comparison = ev._compare_baseline({"answer_match": 1.0}, {"summary": {}})

    assert comparison["answer_match"] is None


def test_report_contains_no_absolute_paths_or_private_fields():
    """脚本层报告不泄露本地路径与证据私密字段。"""
    records = [ev._run_scripted_case(case) for case in load_cases(ev.DEFAULT_CASES)]
    payload = json.dumps(records, ensure_ascii=False)

    assert "file_path" not in payload
    assert str(Path.cwd()) not in payload


def test_require_real_workflow_rejects_fake_workflow():
    """端到端层误用假工作流必须直接失败，不能静默降级。"""
    with pytest.raises(RuntimeError, match="端到端评测必须使用真实 WorkflowManager"):
        ev._require_real_workflow(ev.EvalWorkflow({}))


def test_require_real_workflow_accepts_real_manager():
    """真实 WorkflowManager 实例通过校验。"""
    ev._require_real_workflow(object.__new__(WorkflowManager))


def test_require_real_workflow_rejects_mock_with_real_spec():
    """带真实类 spec 的 Mock 仍不是真实管理器，必须拒绝。"""
    with pytest.raises(RuntimeError, match="端到端评测必须使用真实 WorkflowManager"):
        ev._require_real_workflow(MagicMock(spec=WorkflowManager))


def test_e2e_cases_reject_injected_fake_factory():
    """端到端构造若被注入假工作流，在运行用例前就失败。"""
    with pytest.raises(RuntimeError, match="端到端评测必须使用真实 WorkflowManager"):
        ev._e2e_cases(
            [{"id": "lit", "category": "literature", "input": "TP53"}],
            None,
            "knowledge_base",
            workflow_factory=lambda provider, knowledge_dir: ev.EvalWorkflow({}),
        )


def test_e2e_cases_require_literature_cases():
    """端到端层只跑真实检索用例；没有可用用例时直接失败。"""
    with pytest.raises(RuntimeError, match="没有可用用例"):
        ev._e2e_cases(
            [{"id": "greeting", "category": "no_tool", "input": "你好"}],
            None,
            "knowledge_base",
            workflow_factory=lambda provider, knowledge_dir: object.__new__(WorkflowManager),
        )


def test_tool_selection_summary_does_not_report_answer_quality():
    """假工作流层只报告工具选择与格式，回答质量留空。"""
    summary = ev._summary([_record(answer_match=None)], 0.0, 0.0, ev.MODE_TOOL_SELECTION)

    assert summary["tool_selection_accuracy"] == 1.0
    assert summary["citation_validity"] == 1.0
    assert summary["answer_match"] is None


def test_e2e_summary_reports_answer_quality_without_tool_metrics():
    """端到端层报告回答与引用质量；假工作流工具记录不参与。"""
    summary = ev._summary(
        [_record(tool_selection_ok=None, actual_tools=None)], 0.0, 0.0, ev.MODE_E2E
    )

    assert summary["tool_selection_accuracy"] is None
    assert summary["answer_match"] == 1.0
    assert summary["citation_validity"] == 1.0


def test_live_is_deprecated_alias_for_tool_selection(monkeypatch, tmp_path):
    """旧 --live 仍可用，但报告标记为工具选择层。"""
    monkeypatch.setattr(ev, "load_cases", lambda path: [{"id": "lit"}])
    monkeypatch.setattr(
        ev, "_tool_selection_cases", lambda cases, provider: [_record(answer_match=None)]
    )
    out = tmp_path / "report.json"

    code = ev.main(["--live", "--out", str(out)])

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["mode"] == ev.MODE_TOOL_SELECTION
    assert report["baseline_comparable"] is False
    assert report["summary"]["answer_match"] is None
    assert code == 0


def test_baseline_is_rejected_outside_e2e(tmp_path):
    """基线只属于端到端层；脚本层传入直接报错。"""
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"mode": "e2e", "summary": {}}), encoding="utf-8")

    with pytest.raises(SystemExit):
        ev.main(["--scripted", "--baseline", str(path)])


def test_baseline_with_old_schema_is_rejected(tmp_path):
    """旧版报告不能与新格式比较。"""
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps({"mode": "e2e", "schema_version": "knowledge-tool-eval/1.0.0"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        ev.main(["--e2e", "--baseline", str(path)])
