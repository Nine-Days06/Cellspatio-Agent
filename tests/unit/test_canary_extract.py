"""canary_extract：实体质量判定（纯逻辑，注入假实体名）。"""

from canary_extract import judge_entities


def test_judge_flags_fake_ids_and_cjk_and_bibliographic():
    names = ["NCBIGene:947", "CD34", "造血干细胞", "NCBIGene:E-cadherin",
             "9704485", "10.1016/j.yexcr.2019.111568", "Experimental Cell Research"]
    report = judge_entities(names)
    assert report["fake_id"] == ["NCBIGene:E-cadherin"]
    assert report["cjk"] == ["造血干细胞"]
    assert set(report["bibliographic"]) >= {"9704485", "10.1016/j.yexcr.2019.111568"}
    assert report["ok"] is False


def test_judge_ok_on_clean_names():
    report = judge_entities(["NCBIGene:947", "CD34", "T cell", "UMAP"])
    assert report["ok"] is True
