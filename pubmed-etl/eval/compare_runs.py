"""
多版本提示词横向对比：同一金标下比各 run 的指标与误判明细。

用法：
  cd pubmed-etl
  python -m eval.compare_runs --runs data/eval_v2/run_baseline_v1.jsonl data/eval_v2/run_candidate_v2.jsonl
"""

import argparse
import json
from pathlib import Path

from eval.score_prompt_eval import apply_gold, confusion, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True, help="若干 run_prompt_eval 输出 JSONL")
    parser.add_argument("--gold", default="data/eval_v2/gold.jsonl", help="金标 JSONL（唯一真源）")
    args = parser.parse_args()

    gold_path = Path(args.gold)
    print("| 版本 | acc | prec | rec | F1 | kappa | TP | FP | TN | FN |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for run in args.runs:
        path = Path(run)
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows = apply_gold(rows, gold_path)
        m = confusion(rows)
        mt = metrics(m)
        print(
            f"| {path.stem} | {mt['accuracy']:.3f} | {mt['precision']:.3f} | {mt['recall']:.3f} "
            f"| {mt['f1']:.3f} | {mt['kappa']:.3f} | {m['TP']} | {m['FP']} | {m['TN']} | {m['FN']} |"
        )

    print("\n误判明细：")
    for run in args.runs:
        path = Path(run)
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows = apply_gold(rows, gold_path)
        bad = [
            f"{'FP' if r['pred'] == 'RELEVANT' else 'FN'}:{r['sample_id']}"
            for r in rows
            if r["pred"] != r["gold"]
        ]
        print(f"  {path.stem}: {' '.join(bad) if bad else '(无)'}")


if __name__ == "__main__":
    main()
