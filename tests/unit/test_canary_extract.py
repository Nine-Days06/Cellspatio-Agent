"""canary_extract：实体质量判定与批次读取。"""

import pytest

import canary_extract
from canary_extract import _load_articles, _read_entity_names, judge_entities, main
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


def test_read_entity_names_returns_none_without_backend():
    """无法确定后端时返回 None，调用方不能当成空库放行。"""
    client = type("Stub", (), {"working_dir": None, "workspace": None})()
    assert _read_entity_names(client) is None


def test_read_entity_names_reads_file_backend(tmp_path):
    """文件后端应读到实体名，并统一为小写。"""
    (tmp_path / "kv_store_full_entities.json").write_text(
        '{"e1": {"entity_names": ["CD34", "TP53"]}}', encoding="utf-8"
    )
    client = type("Stub", (), {"working_dir": tmp_path, "workspace": "canary"})()
    assert _read_entity_names(client) == ["cd34", "tp53"]


def test_canary_fails_when_entities_unreadable(monkeypatch):
    """实体不可读时质量门必须失败，不能以空样本报 ok。"""

    def _boom(**kwargs):
        raise RuntimeError("无法读取金丝雀知识库实体，不能判定质量门")

    monkeypatch.setattr(canary_extract, "run_canary", _boom)
    assert main(["--n", "1"]) == 1
