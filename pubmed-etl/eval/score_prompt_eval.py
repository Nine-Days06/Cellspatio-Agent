"""
提示词评测计分器：金标 × 运行结果 → 指标 + Markdown 报告。

用法：
  cd pubmed-etl
  python -m eval.score_prompt_eval --run data/eval_v2/runs/baseline_v1.jsonl

输出：同名 <run>_report.md（总体 + 分层指标 + 误判样例索引）。
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def confusion(rows: list[dict]) -> dict:
    """以 RELEVANT 为正类统计混淆矩阵（ERROR 计入解析失败）。"""
    m = {"TP": 0, "FP": 0, "TN": 0, "FN": 0, "ERROR": 0}
    for r in rows:
        g, p = r["gold"], r["pred"]
        if p == "ERROR":
            m["ERROR"] += 1
        elif g == "RELEVANT" and p == "RELEVANT":
            m["TP"] += 1
        elif g == "NOT_RELEVANT" and p == "RELEVANT":
            m["FP"] += 1
        elif g == "NOT_RELEVANT" and p == "NOT_RELEVANT":
            m["TN"] += 1
        else:
            m["FN"] += 1
    return m


def metrics(m: dict) -> dict:
    """accuracy/precision/recall/F1/kappa（ERROR 不计入分母，单独报）。"""
    total = m["TP"] + m["FP"] + m["TN"] + m["FN"]
    if total == 0:
        return {}
    acc = (m["TP"] + m["TN"]) / total
    prec = m["TP"] / (m["TP"] + m["FP"]) if m["TP"] + m["FP"] else 0.0
    rec = m["TP"] / (m["TP"] + m["FN"]) if m["TP"] + m["FN"] else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    pos = m["TP"] + m["FN"]
    neg = m["TN"] + m["FP"]
    pe = ((m["TP"] + m["FP"]) * pos + (m["TN"] + m["FN"]) * neg) / (total * total) if total else 0.0
    kappa = (acc - pe) / (1 - pe) if pe < 1 else 0.0
    return {"n": total, "accuracy": acc, "precision": prec, "recall": rec, "f1": f1, "kappa": kappa}


def apply_gold(rows: list[dict], gold_path: Path) -> list[dict]:
    """用 gold.jsonl 的最新金标覆盖 run 文件里的旧快照。

    run 文件里的 gold 是评测当时的快照；金标一旦修订（本项目修订过多轮），
    直接计分会算出错误指标。所以 --gold 指向的金标文件是唯一真源。
    """
    gold = {}
    for line in gold_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            gold[d["sample_id"]] = d["gold"]
    missing = [r["sample_id"] for r in rows if r["sample_id"] not in gold]
    if missing:
        raise SystemExit(f"金标缺失 {len(missing)} 条：{missing[:5]}")
    return [{**r, "gold": gold[r["sample_id"]]} for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run_prompt_eval 输出的 JSONL")
    parser.add_argument(
        "--gold", default="data/eval_v2/gold.jsonl", help="金标 JSONL（唯一真源，覆盖 run 内快照）"
    )
    parser.add_argument("--max-examples", type=int, default=15, help="每类误判最多列出的样例数")
    args = parser.parse_args()

    run_path = Path(args.run)
    rows = [json.loads(x) for x in run_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = apply_gold(rows, Path(args.gold))
    overall = metrics(confusion(rows))

    by_layer: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_layer[r.get("layer", "?")].append(r)

    lines = [f"# 提示词评测报告：{run_path.name}", ""]
    lines.append(f"- 样本总数：{len(rows)}（含 ERROR {confusion(rows)['ERROR']}）")
    lines.append(
        f"- 总体：accuracy {overall['accuracy']:.3f} | precision {overall['precision']:.3f} "
        f"| recall {overall['recall']:.3f} | F1 {overall['f1']:.3f} | kappa {overall['kappa']:.3f}"
    )
    lines.append("")
    lines.append("## 分层指标")
    lines.append("| 层 | n | accuracy | FP | FN | ERROR |")
    lines.append("|---|---|---|---|---|---|")
    for layer in sorted(by_layer):
        m = confusion(by_layer[layer])
        mt = metrics(m)
        lines.append(
            f"| {layer} | {mt.get('n', 0)} | {mt.get('accuracy', 0):.3f} | {m['FP']} | {m['FN']} | {m['ERROR']} |"
        )

    lines.append("")
    lines.append("## 误判样例")
    for tag, keep in (
        ("FP（误收）", lambda r: r["pred"] == "RELEVANT" and r["gold"] == "NOT_RELEVANT"),
        ("FN（漏收）", lambda r: r["pred"] == "NOT_RELEVANT" and r["gold"] == "RELEVANT"),
    ):
        bad = [r for r in rows if keep(r)]
        lines.append("")
        lines.append(f"### {tag}（{len(bad)}）")
        for r in bad[: args.max_examples]:
            lines.append(f"- {r['sample_id']} [{r.get('layer', '?')}] {r.get('reason', '')}")

    report = run_path.with_name(run_path.stem + "_report.md")
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"报告已写入 {report}")


if __name__ == "__main__":
    main()
