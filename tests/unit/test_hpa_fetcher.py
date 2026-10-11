"""HPA（Human Protein Atlas）fetcher 单测（httpx.MockTransport 全 mock）。"""

import httpx

from src.data.fetchers.hpa_fetcher import HPAFetcher


def _fetcher_with(handler):
    return HPAFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _search_payload():
    """search=CD4 返回 155 条模糊结果（实测），首条即精确匹配 CD4"""
    return [
        {"Gene": "CD4", "Gene synonym": [], "Ensembl": "ENSG00000010610",
         "Uniprot": ["P01730"], "Protein class": ["CD markers", "Disease related genes"],
         "Evidence": "Evidence at protein level", "Subcellular main location": ["Plasma membrane"]},
        {"Gene": "CD40LG", "Gene synonym": ["CD154"], "Ensembl": "ENSG00000102245",
         "Uniprot": ["P29965"], "Protein class": ["CD markers"],
         "Evidence": "Evidence at protein level", "Subcellular main location": ["Golgi apparatus"]},
        {"Gene": "CD40", "Gene synonym": ["Bp50"], "Ensembl": "ENSG00000101017",
         "Uniprot": ["P25942"], "Protein class": ["CD markers"],
         "Evidence": "Evidence at protein level", "Subcellular main location": ["Vesicles"]},
    ]


def test_search_filters_exact_gene_match():
    def handler(request):
        assert "search_download.php" in str(request.url)
        return httpx.Response(200, json=_search_payload())

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("CD4")
    # 模糊结果中只保留精确匹配 CD4 的一条
    assert len(metas) == 1
    assert metas[0].asset_id == "CD4"
    assert metas[0].title == "CD4"
    assert metas[0].asset_type == "knowledge"


def test_confirm_parses_ensembl_and_uniprot():
    def handler(request):
        return httpx.Response(200, json=_search_payload())

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("CD4")
    assert info.title == "CD4"
    assert info.metadata["ensembl"] == "ENSG00000010610"
    assert info.metadata["uniprot"] == ["P01730"]
    assert "CD markers" in info.description


def test_confirm_no_exact_match_raises():
    def handler(request):
        return httpx.Response(200, json=_search_payload())

    fetcher = _fetcher_with(handler)
    try:
        fetcher.confirm("NOPE_GENE")
    except ValueError:
        assert True
    else:
        raise AssertionError("confirm 无精确匹配应抛 ValueError")


def test_ingest_text_renders_protein():
    def handler(request):
        return httpx.Response(200, json=_search_payload())

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("CD4")
    assert text.startswith("# HPA 蛋白: CD4")
    assert "来源：https://www.proteinatlas.org/ENSG00000010610" in text
    assert "Ensembl：ENSG00000010610" in text
    assert "UniProt：P01730" in text
    assert "蛋白类别：CD markers; Disease related genes" in text
    assert "证据：Evidence at protein level" in text
    assert "亚细胞定位：Plasma membrane" in text


def test_ingest_text_skips_missing_fields():
    """缺失的列（如 CD19 的 Subcellular main location 为 null）对应行省略"""
    def handler(request):
        return httpx.Response(200, json=[{
            "Gene": "CD19", "Gene synonym": [], "Ensembl": "ENSG00000177455",
            "Uniprot": ["P15391"], "Protein class": [],
            "Evidence": "Evidence at protein level", "Subcellular main location": None,
        }])

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("CD19")
    assert "Ensembl：ENSG00000177455" in text
    assert "蛋白类别：无" in text
    assert "亚细胞定位：" not in text  # None → 整行省略


def test_download_raises_not_implemented():
    fetcher = HPAFetcher()
    try:
        fetcher.download("CD4")
    except NotImplementedError:
        assert True
    else:
        raise AssertionError("download 应抛 NotImplementedError")
