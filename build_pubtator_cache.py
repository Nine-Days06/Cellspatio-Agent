"""预计算 PMID→基因 CURIE 缓存（PubTator3），供文献导入注入规范 token。

用法（在跑 24k 文献导入之前执行一次；纯 HTTP，不碰 LLM）：
    python build_pubtator_cache.py --csv pubmed-etl/data/output/articles_<ts>.csv

缓存写到 data/raw/pubtator/gene_ids.json；导入时 knowledge_importer 读取注入
`基因ID：NCBIGene:…`。可重复生成（按 PMID 覆盖写）。
"""

import argparse
import csv
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.data.pubtator import CACHE_PATH, fetch_pmid_genes, save_pmid_genes


def read_pmids(csv_path: Path) -> list[str]:
    """读 ETL 导出 CSV 的 pmid 列（去重保序）"""
    seen: dict[str, None] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            pmid = (row.get("pmid") or "").strip()
            if pmid:
                seen.setdefault(pmid, None)
    return list(seen)


def build_cache(
    csv_path: Path | str,
    *,
    fetch: Callable[[list[str]], dict[str, list[str]]] | None = None,
    cache_path: Path | str | None = None,
) -> dict:
    """读 CSV PMID → 查 PubTator3 → 写缓存；返回统计（fetch 可注入以便离线测试）"""
    fetch = fetch or fetch_pmid_genes
    pmids = read_pmids(Path(csv_path))
    genes = fetch(pmids)
    save_pmid_genes(genes, cache_path)
    counts = [len(v) for v in genes.values()]
    return {
        "pmids": len(pmids),
        "hit": len(genes),
        "genes": sum(counts),
        "max_genes": max(counts, default=0),
        "cache": str(cache_path or CACHE_PATH),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="预计算 PubTator3 PMID→基因 缓存")
    parser.add_argument("--csv", required=True, help="ETL 导出 CSV（含 pmid 列）")
    args = parser.parse_args(argv)
    stats = build_cache(args.csv)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
