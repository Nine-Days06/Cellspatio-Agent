"""重排离线 A/B：同一候选集对比重排关/开。"""

from __future__ import annotations

from typing import Any

from evals.run_knowledge_rerank_eval import (
    DEFAULT_CASES,
    evaluate,
    load_cases,
    run_case,
)


def _case() -> dict[str, Any]:
    """相关片段排在末尾，重排开启后应显著提升 nDCG。"""
    return {
        "id": "ab-case",
        "query": "TP53 细胞周期阻滞",
        "scope": "answer",
        "cutoff": 3,
        "candidates": [
            {"chunk_id": "c1", "pmid": "1", "grade": 0, "text": "unrelated one"},
            {"chunk_id": "c2", "pmid": "2", "grade": 0, "text": "unrelated two"},
            {"chunk_id": "c3", "pmid": "3", "grade": 2, "text": "TP53 induces arrest"},
            {"chunk_id": "c4", "pmid": "4", "grade": 1, "text": "TP53 pathway"},
        ],
    }


def test_rerank_ab_uses_the_same_candidates_and_improves_ndcg() -> None:
    """两侧共用候选集；oracle 重排把相关片段提前。"""
    record = run_case(_case())

    assert record["candidate_count"] == 4
    assert record["off"]["rerank_status"] == "disabled"
    assert record["on"]["rerank_status"] == "applied"
    assert record["on"]["ndcg"] > record["off"]["ndcg"]
    assert record["delta_ndcg"] > 0


def test_rerank_ab_report_recommends_real_retest() -> None:
    """离线提升只给出复测建议，不直接宣称质量结论。"""
    report = evaluate([_case()])

    assert report["scorer"] == "oracle"
    assert report["summary"]["case_count"] == 1
    assert report["summary"]["delta_ndcg"] > 0
    assert "复测" in report["verdict"]


def test_rerank_ab_fixture_runs_offline() -> None:
    """仓库内用例可离线跑通，三档 scope 都能产出指标。"""
    cases = load_cases(DEFAULT_CASES)

    report = evaluate(cases)

    assert report["summary"]["case_count"] == len(cases)
    assert {record["scope"] for record in report["cases"]} == {
        "answer",
        "survey",
        "exhaustive",
    }
