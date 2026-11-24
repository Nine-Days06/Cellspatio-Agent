"""QA 跑批退出码：只有全量基础设施失败才判非零。"""

from __future__ import annotations

from evals.run_qa_eval import exit_code


def test_exit_code_all_errors_is_nonzero():
    """全部用例 error 时不能报成功。"""
    assert exit_code({"summary": {"case_count": 12, "status_counts": {"error": 12}}}) == 1


def test_exit_code_partial_errors_stays_zero():
    """部分失败仍写出报告，退出码保持 0 供技术排查。"""
    report = {"summary": {"case_count": 12, "status_counts": {"success": 11, "error": 1}}}
    assert exit_code(report) == 0


def test_exit_code_empty_report_stays_zero():
    """空报告不当作全量失败。"""
    assert exit_code({}) == 0
