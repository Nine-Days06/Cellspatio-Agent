"""build_pubtator_cache：读 CSV PMID → 建缓存（假 fetch，不联网）。"""

import json

import build_pubtator_cache as bpc

_CSV = (
    "pmid,title,abstract\n"
    "9704485,T,A\n"
    "9689130,T,A\n"
    "9704485,T,A\n"  # 重复 → 去重
)


def test_read_pmids_dedups_preserving_order(tmp_path):
    csv_path = tmp_path / "articles.csv"
    csv_path.write_text(_CSV, encoding="utf-8-sig")
    assert bpc.read_pmids(csv_path) == ["9704485", "9689130"]


def test_build_cache_writes_and_reports(tmp_path):
    csv_path = tmp_path / "articles.csv"
    csv_path.write_text(_CSV, encoding="utf-8-sig")
    cache_path = tmp_path / "gene_ids.json"

    stats = bpc.build_cache(
        csv_path,
        fetch=lambda pmids: {"9704485": ["NCBIGene:947", "NCBIGene:952"]},
        cache_path=cache_path,
    )

    assert stats["pmids"] == 2
    assert stats["hit"] == 1
    assert stats["genes"] == 2
    assert stats["max_genes"] == 2
    assert json.loads(cache_path.read_text(encoding="utf-8")) == {
        "9704485": ["NCBIGene:947", "NCBIGene:952"]
    }
