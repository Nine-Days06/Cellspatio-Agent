"""重排开关离线 A/B：同一候选集对比重排关/开的选择质量。

用法：
  python -m evals.run_knowledge_rerank_eval --cases evals/knowledge_rerank_cases.jsonl

用例格式（JSONL）：``id``、``query``、``scope``（answer/survey/exhaustive）、
``cutoff``、``candidates``（``chunk_id``/``pmid``/``text``/``grade``）。

离线层用 grade 当假打分器（oracle），只验证选择管线与指标；它不能替代真实
提供方的质量结论，默认开启前必须用真实 endpoint 复测。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.qa import retrieve_evidence  # noqa: E402
from src.knowledge.rerank import RerankConfig  # noqa: E402

DEFAULT_CASES = ROOT / "evals" / "knowledge_rerank_cases.jsonl"
REPORT_SCHEMA = "knowledge-rerank-ab/1.0.0"


def _config_summary() -> dict[str, Any]:
    """记录本次运行的选择管线参数，便于跨运行比较。"""
    config = RerankConfig()
    return {
        "scorer": "oracle",
        "timeout_seconds": config.timeout,
        "batch_size": config.batch_size,
        "max_candidates": config.max_candidates,
        "note": "oracle 打分器不做候选截断，max_candidates 仅约束 HTTP 适配器",
    }


class FixedKnowledge:
    """回放一组固定候选片段。"""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.result = {"status": "success", "data": {"chunks": chunks}, "metadata": {}}

    def query_data(self, question: str, mode: str = "mix", **kwargs: Any) -> Any:
        """忽略参数，返回固定候选集。"""
        return self.result


class OracleReranker:
    """用人工 grade 当分数；只用于离线验证，不代表真实模型。"""

    fingerprint = "offline:oracle"

    def __init__(self, grades: list[float]) -> None:
        self.grades = grades

    def score(self, query: str, passages: list[str]) -> list[float]:
        """分数与候选顺序对齐。"""
        return self.grades[: len(passages)]


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """读取 JSONL 用例，要求候选带 grade 与可核对身份。"""
    cases: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    for case in cases:
        if not case.get("candidates"):
            raise ValueError(f"用例 {case.get('id')} 没有候选片段")
    return cases


def _to_chunks(case: dict[str, Any]) -> list[dict[str, Any]]:
    """把用例候选转成 LightRAG chunk 形状；身份来自 pmid 字段。"""
    return [
        {
            "chunk_id": candidate.get("chunk_id") or f"{case['id']}-c{index}",
            "pmid": candidate["pmid"],
            "content": candidate["text"],
        }
        for index, candidate in enumerate(case["candidates"])
    ]


def _retrieval_kwargs(case: dict[str, Any]) -> dict[str, Any]:
    """按 scope 选择与生产一致的选择参数。"""
    cutoff = int(case.get("cutoff") or 8)
    scope = case.get("scope") or "answer"
    if scope == "survey":
        return {
            "scope": scope,
            "max_evidence": cutoff * 2,
            "content_chars": 1200,
            "max_sources": cutoff,
            "per_source_max": 2,
        }
    if scope == "exhaustive":
        return {"scope": scope, "page_sources": cutoff}
    return {"scope": "answer", "max_evidence": cutoff, "content_chars": 0}


def _dcg(grades: list[int]) -> float:
    """折扣累计增益。"""
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def _metrics(
    evidence: list[dict[str, Any]], grades: dict[str, int], cutoff: int, ideal: list[int]
) -> dict[str, Any]:
    """把一次选择结果换算成相关性指标。"""
    returned = [grades.get(str(item.get("chunk_id")), 0) for item in evidence][:cutoff]
    dcg = _dcg(returned)
    idcg = _dcg(sorted(ideal, reverse=True)[:cutoff])
    first_relevant = next((index + 1 for index, grade in enumerate(returned) if grade > 0), 0)
    return {
        "ndcg": round(dcg / idcg, 4) if idcg else 0.0,
        "mrr": round(1.0 / first_relevant, 4) if first_relevant else 0.0,
        "hit_at_cutoff": any(grade > 0 for grade in returned),
        "relevant_count": sum(1 for grade in returned if grade > 0),
        "source_count": len({str(item.get("source_id")) for item in evidence}),
        "returned": len(evidence),
    }


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """同一候选集跑重排关/开，返回两侧指标。"""
    chunks = _to_chunks(case)
    grades = {
        str(chunk["chunk_id"]): int(candidate["grade"])
        for chunk, candidate in zip(chunks, case["candidates"], strict=True)
    }
    ideal = list(grades.values())
    cutoff = int(case.get("cutoff") or 8)
    kwargs = _retrieval_kwargs(case)
    sides: dict[str, Any] = {}
    for side, reranker in (
        ("off", None),
        ("on", OracleReranker([float(grade) for grade in ideal])),
    ):
        retrieval = retrieve_evidence(
            FixedKnowledge(chunks),
            str(case["query"]),
            deadline=time.monotonic() + 5,
            reranker=reranker,
            **kwargs,
        )
        if retrieval.error is not None:
            raise RuntimeError(f"用例 {case['id']} 检索失败：{retrieval.error.message}")
        sides[side] = {
            "rerank_status": retrieval.rerank_status,
            **_metrics(retrieval.evidence, grades, cutoff, ideal),
        }
    return {
        "id": case["id"],
        "query": case["query"],
        "scope": kwargs["scope"],
        "cutoff": cutoff,
        "candidate_count": len(chunks),
        "off": sides["off"],
        "on": sides["on"],
        "delta_ndcg": round(sides["on"]["ndcg"] - sides["off"]["ndcg"], 4),
    }


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总所有用例并给出是否建议默认开启的离线结论。"""
    records = [run_case(case) for case in cases]
    summary: dict[str, Any] = {"case_count": len(records)}
    for side in ("off", "on"):
        summary[side] = {
            key: round(statistics.mean(record[side][key] for record in records), 4)
            for key in ("ndcg", "mrr", "relevant_count", "source_count")
        }
        summary[side]["hit_rate"] = round(
            statistics.mean(1.0 if record[side]["hit_at_cutoff"] else 0.0 for record in records),
            4,
        )
    delta = round(summary["on"]["ndcg"] - summary["off"]["ndcg"], 4)
    summary["delta_ndcg"] = delta
    if delta > 0.01:
        verdict = "离线 oracle 显示提升；默认开启前需用真实提供方复测"
    elif delta < -0.01:
        verdict = "离线 oracle 显示下降，不应默认开启"
    else:
        verdict = "离线无显著差异，保持默认关闭"
    return {
        "schema_version": REPORT_SCHEMA,
        "scorer": "oracle",
        "note": "grade 当假打分器，仅验证选择管线与指标，不代表真实重排模型质量",
        "config": _config_summary(),
        "summary": summary,
        "verdict": verdict,
        "cases": records,
    }


def main(argv: list[str] | None = None) -> int:
    """读取用例、输出报告；离线层不依赖任何重排服务。"""
    parser = argparse.ArgumentParser(description="文献重排开关离线 A/B")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    report = evaluate(load_cases(args.cases))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
