"""真实公共数据库 API 集成测试

默认跳过，需设置 RUN_NETWORK_TESTS=1 才会运行。
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1",
    reason="需真实网络，设置 RUN_NETWORK_TESTS=1 运行",
)


def test_kegg_real_search():
    from src.data.fetchers.kegg_fetcher import KEGGFetcher

    fetcher = KEGGFetcher()
    metas = fetcher.search("glycolysis", max_results=3)
    assert metas
    assert all(m.source == "kegg" for m in metas)


def test_kegg_real_get():
    from src.data.fetchers.kegg_fetcher import KEGGFetcher

    fetcher = KEGGFetcher()
    text = fetcher.ingest_text("hsa00010")
    assert "Glycolysis" in text


def test_uniprot_real_search():
    from src.data.fetchers.uniprot_fetcher import UniProtFetcher

    fetcher = UniProtFetcher()
    metas = fetcher.search("TP53 AND organism_id:9606", max_results=3)
    assert metas
    assert any(m.asset_id == "P04637" for m in metas)


def test_uniprot_real_get():
    from src.data.fetchers.uniprot_fetcher import UniProtFetcher

    fetcher = UniProtFetcher()
    text = fetcher.ingest_text("P04637")
    assert "P04637" in text


def test_geo_real_search():
    from src.data.fetchers.geo_fetcher import GEOFetcher

    fetcher = GEOFetcher()
    metas = fetcher.search("type:gse AND human AND diabetes", max_results=3)
    assert metas
    assert all(m.asset_id.startswith("GSE") for m in metas)