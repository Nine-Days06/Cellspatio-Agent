"""提示词评测的配置校验与全 ERROR 计分行为。"""

import json
import sys
from pathlib import Path

import pytest

ETL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ETL_ROOT))

from eval import run_prompt_eval, score_prompt_eval  # noqa: E402


def test_eval_model_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 OPENAI_MODEL 时给出明确错误，而不是 KeyError。"""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_MODEL"):
        run_prompt_eval._eval_model()


def test_score_returns_nonzero_when_all_rows_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全部样本 ERROR 时没有可计分指标，命令必须失败。"""
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps({"sample_id": "s1", "gold": "RELEVANT"}) + "\n",
        encoding="utf-8",
    )
    run = tmp_path / "run.jsonl"
    run.write_text(
        json.dumps({"sample_id": "s1", "layer": "L1", "gold": "RELEVANT", "pred": "ERROR"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["score_prompt_eval", "--run", str(run), "--gold", str(gold)],
    )

    assert score_prompt_eval.main() == 1
    report = run.with_name("run_report.md")
    assert "无可计分样本" in report.read_text(encoding="utf-8")


def test_score_returns_zero_with_scored_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有可计分样本时正常返回 0。"""
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps({"sample_id": "s1", "gold": "RELEVANT"}) + "\n",
        encoding="utf-8",
    )
    run = tmp_path / "run.jsonl"
    run.write_text(
        json.dumps({"sample_id": "s1", "layer": "L1", "gold": "RELEVANT", "pred": "RELEVANT"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["score_prompt_eval", "--run", str(run), "--gold", str(gold)],
    )

    assert score_prompt_eval.main() == 0
