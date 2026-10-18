"""
从本地库分层抽样构建提示词评测金标集（eval_v2）。

前置：已跑完 download → parse → clean。

分层依据（只看标题+摘要关键词与 article_types，硬过滤通过 = 不在 filter_log 的 hard_filter 阶段）：
  L2a 蛋白组（CyTOF / IMC / MIBI / CODEX / 单细胞蛋白组 / 空间蛋白组）   60
  L2b 空间组学变体（spatial omics / spatially resolved / spatial genomics…） 40
  L2c 杂项变体（RNA velocity / cell atlas / single-cell sequencing）      20
  L3  综述（article_types 含 Review）                                    50
  L4  观点类（article_types 含 Letter/Comment/Editorial）                 40
  L5  "spatially resolved" 纯噪声层（不含任何组学词）                     40

用法：
  cd pubmed-etl
  python -m eval.sample_from_db [--out ...] [--seed ...]

输出 JSONL：{sample_id, layer, pmid, title, abstract, pub_types, year, journal, mesh_terms}
"""

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.settings import DB_PATH  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "eval_v2"
SEED = 20261018

# 组学词（L5 噪声层排除用）
OMICS = (
    "hay LIKE '%transcriptom%' OR hay LIKE '%proteom%' OR hay LIKE '%genom%' "
    "OR hay LIKE '%epigenom%' OR hay LIKE '%metabolom%' OR hay LIKE '%multiomic%' "
    "OR hay LIKE '%multi-omic%' OR hay LIKE '%mass cytometry%' OR hay LIKE '%cytof%' "
    "OR hay LIKE '%mibi%' OR hay LIKE '%sequencing%' OR hay LIKE '%rna-seq%'"
)

# 每层：(层名, 条件, 抽样数)
LAYERS = [
    (
        "L2a_proteomics",
        "(hay LIKE '%cytof%' OR hay LIKE '%imaging mass cytometry%' OR hay LIKE '%mass cytometry%' "
        "OR hay LIKE '%mibi%' OR hay LIKE '%codex%' OR hay LIKE '%single-cell proteom%' "
        "OR hay LIKE '%spatial proteom%')",
        60,
    ),
    (
        "L2b_spatial_variants",
        "(hay LIKE '%spatial omics%' OR hay LIKE '%spatial multi-omics%' OR hay LIKE '%spatially resolved%' "
        "OR hay LIKE '%spatial genomics%' OR hay LIKE '%spatial epigenomics%' OR hay LIKE '%spatial metabolomics%')",
        40,
    ),
    (
        "L2c_misc_variants",
        "(hay LIKE '%rna velocity%' OR hay LIKE '%single-cell atlas%' OR hay LIKE '%cell atlas%' "
        "OR hay LIKE '%single-cell sequencing%')",
        20,
    ),
    ("L3_review", "article_types LIKE '%Review%'", 50),
    (
        "L4_opinion",
        "(article_types LIKE '%Letter%' OR article_types LIKE '%Comment%' OR article_types LIKE '%Editorial%')",
        40,
    ),
    ("L5_noise", f"(hay LIKE '%spatially resolved%' AND NOT ({OMICS}))", 40),
]

SAMPLE_ORDER = [
    "L5_noise",
    "L2a_proteomics",
    "L2b_spatial_variants",
    "L3_review",
    "L4_opinion",
    "L2c_misc_variants",
]

BASE_SQL = """
WITH src AS (
    SELECT a.*, lower(coalesce(a.title, '') || ' ' || coalesce(a.abstract, '')) AS hay
    FROM articles a
    WHERE a.pmid NOT IN (SELECT pmid FROM filter_log WHERE stage = 'hard_filter')
)
SELECT pmid, title, abstract, article_types, pub_year, journal, mesh_terms
FROM src
WHERE {cond}
"""


def fetch_layer(conn: sqlite3.Connection, cond: str, exclude: set[str]) -> list[dict]:
    """按层条件取全部候选，排除已选 PMID。"""
    rows = conn.execute(BASE_SQL.format(cond=cond)).fetchall()
    return [dict(r) for r in rows if r["pmid"] not in exclude]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_DIR / "samples.jsonl"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    passed = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE pmid NOT IN "
        "(SELECT pmid FROM filter_log WHERE stage = 'hard_filter')"
    ).fetchone()[0]
    print(f"articles {total} 条，硬过滤通过 {passed} 条")

    rng = random.Random(args.seed)
    chosen: set[str] = set()
    samples: list[dict] = []
    conds = {n: c for n, c, _ in LAYERS}
    sizes = {n: s for n, _, s in LAYERS}

    for name in SAMPLE_ORDER:
        pool = fetch_layer(conn, conds[name], chosen)
        picks = rng.sample(pool, min(sizes[name], len(pool)))
        print(f"{name}: 池 {len(pool)} → 抽 {len(picks)}")
        for rec in picks:
            chosen.add(rec["pmid"])
            rec["layer"] = name
            rec["pub_types"] = (rec.pop("article_types") or "").split("|")
            rec["year"] = str(rec.pop("pub_year") or "")
            rec["mesh_terms"] = (rec["mesh_terms"] or "").split("|")
            samples.append(rec)

    conn.close()

    samples.sort(key=lambda r: (r["layer"], int(r["pmid"])))
    counters: dict[str, int] = {}
    for rec in samples:
        counters[rec["layer"]] = counters.get(rec["layer"], 0) + 1
        rec["sample_id"] = f"{rec['layer'][:3]}-{counters[rec['layer']]:03d}"

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for rec in samples:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"共 {len(samples)} 条 → {out}")


if __name__ == "__main__":
    main()
