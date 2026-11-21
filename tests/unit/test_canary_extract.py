"""canary_extract：实体质量判定与批次读取。"""

import pytest

import canary_extract
from canary_extract import _load_articles, judge_entities
from src.article_batches import ArticleBatchError


def test_judge_flags_fake_ids_and_cjk_and_bibliographic():
    names = [
        "NCBIGene:947",
        "CD34",
        "造血干细胞",
        "NCBIGene:E-cadherin",
        "9704485",
        "10.1016/j.yexcr.2019.111568",
        "Experimental Cell Research",
    ]
    report = judge_entities(names, banned=["Experimental Cell Research", "Han Shuangshuang"])
    assert report["fake_id"] == ["NCBIGene:E-cadherin"]
    assert report["cjk"] == ["造血干细胞"]
    assert set(report["bibliographic"]) >= {
        "9704485",
        "10.1016/j.yexcr.2019.111568",
        "Experimental Cell Research",
    }
    assert report["ok"] is False


def test_judge_flags_title_like():
    long_title = (
        "Single cell transcriptomic analysis reveals the heterogeneity and "
        "trajectory of immune cells during tumor progression in human patients"
    )
    report = judge_entities(["CD34", long_title])
    assert report["title_like"] == [long_title]
    assert report["ok"] is False


def test_judge_ok_on_clean_names():
    report = judge_entities(["NCBIGene:947", "CD34", "T cell", "UMAP"])
    assert report["ok"] is True


def test_load_articles_only_contract_batches(tmp_path, monkeypatch):
    """canary 不接受任意 CSV，只读 articles_<ts>.csv。"""
    import_dir = tmp_path / "data" / "import"
    import_dir.mkdir(parents=True)
    header = ",".join(("pmid", "title", "abstract"))
    (import_dir / "articles_20260921_100000.csv").write_text(
        f"{header}\n1,Title,Abstract\n", encoding="utf-8-sig"
    )
    (import_dir / "manual.csv").write_text(f"{header}\n2,Manual,Abstract\n", encoding="utf-8-sig")
    monkeypatch.setattr(canary_extract, "ROOT", tmp_path)

    assert [row["pmid"] for row in _load_articles(5)] == ["1"]


def test_load_articles_rejects_empty_contract_set(tmp_path, monkeypatch):
    """只有手工 CSV 时不能静默返回空样本。"""
    import_dir = tmp_path / "data" / "import"
    import_dir.mkdir(parents=True)
    (import_dir / "manual.csv").write_text("pmid,title\n1,T\n", encoding="utf-8-sig")
    monkeypatch.setattr(canary_extract, "ROOT", tmp_path)

    with pytest.raises(ArticleBatchError, match="没有可读的契约文献批次"):
        _load_articles(5)
