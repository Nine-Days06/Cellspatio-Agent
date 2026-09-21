import httpx

from src.data.fetchers.kegg_fetcher import KEGGFetcher


def _fetcher_with(handler):
    return KEGGFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_search_parses_find_response():
    def handler(request):
        assert "/find/pathway/glycolysis" in str(request.url)
        return httpx.Response(200, text=(
            "path:hsa00010\tGlycolysis / Gluconeogenesis - Homo sapiens (human)\n"
            "path:ec00010\tGlycolysis / Gluconeogenesis - Escherichia coli K-12\n"
        ))

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("glycolysis")
    assert len(metas) == 2
    assert metas[0].asset_id == "hsa00010"
    assert metas[0].asset_type == "knowledge"
    assert "Glycolysis" in metas[0].title


def test_search_respects_max_results():
    def handler(request):
        return httpx.Response(200, text="path:one001\tA\npath:one002\tB\npath:one003\tC\n")

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("query", max_results=2)
    assert len(metas) == 2


def test_confirm_parses_name_and_description():
    def handler(request):
        assert "/get/hsa00010" in str(request.url)
        return httpx.Response(200, text=(
            "ENTRY       hsa00010                      Pathway\n"
            "NAME        Glycolysis / Gluconeogenesis\n"
            "DESCRIPTION  Glycolysis is the metabolic pathway\n"
            "xxx\n"
            "//\n"
        ))

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("hsa00010")
    assert info.title == "Glycolysis / Gluconeogenesis"
    assert "metabolic pathway" in info.description


def test_ingest_text_wraps_raw_text():
    def handler(request):
        return httpx.Response(200, text="ENTRY       hsa00010\r\n//\r\n")

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("hsa00010")
    assert text.startswith("# KEGG 通路: hsa00010")
    assert "ENTRY" in text


def test_download_raises_not_implemented():
    fetcher = KEGGFetcher()
    try:
        fetcher.download("hsa00010")
    except NotImplementedError:
        assert True
    else:
        raise AssertionError("download 应抛 NotImplementedError")