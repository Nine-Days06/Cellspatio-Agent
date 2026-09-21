import httpx

from src.data.fetchers.uniprot_fetcher import UniProtFetcher


def _fetcher_with(handler):
    return UniProtFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _search_payload():
    return {
        "results": [{
            "primaryAccession": "P04637",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Cellular tumor antigen p53"}}},
            "genes": [{"geneName": {"value": "TP53"}}],
        }]
    }


def test_search_parses_results():
    def handler(request):
        assert "/uniprotkb/search" in str(request.url)
        assert "P04637" not in request.url.path
        return httpx.Response(200, json=_search_payload())

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("TP53")
    assert len(metas) == 1
    assert metas[0].asset_id == "P04637"
    assert metas[0].title == "Cellular tumor antigen p53"
    assert metas[0].asset_type == "knowledge"


def test_confirm_parses_comments_and_gene():
    def handler(request):
        return httpx.Response(200, json={
            "primaryAccession": "P04637",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Cellular tumor antigen p53"}}},
            "genes": [{"geneName": {"value": "TP53"}}],
            "comments": [
                {"commentType": "FUNCTION",
                 "text": [{"value": "Acts as a tumor suppressor."}]},
            ],
        })

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("P04637")
    assert info.title == "Cellular tumor antigen p53"
    assert info.metadata["gene"] == "TP53"
    assert "tumor suppressor" in info.description


def test_confirm_not_found_raises():
    def handler(request):
        return httpx.Response(404)

    fetcher = _fetcher_with(handler)
    try:
        fetcher.confirm("NOPE0")
    except ValueError:
        assert True
    else:
        raise AssertionError("confirm 应抛 ValueError")


def test_ingest_text_builds_markdown():
    def handler(request):
        assert "/uniprotkb/P04637.json" in str(request.url)
        return httpx.Response(200, json={
            "primaryAccession": "P04637",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "p53"}}},
            "genes": [{"geneName": {"value": "TP53"}}],
            "comments": [{"commentType": "FUNCTION", "text": [{"value": "Tumor suppressor."}]}],
        })

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("P04637")
    assert "# UniProt 蛋白: P04637" in text
    assert "基因：TP53" in text
    assert "Tumor suppressor." in text


def test_search_respects_max_results():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"primaryAccession": f"P0000{i}", "proteinDescription": {"recommendedName": {"fullName": {"value": f"P{i}"}}}}
            for i in range(3)
        ]})

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("query", max_results=3)
    assert len(metas) == 3


def test_download_raises_not_implemented():
    fetcher = UniProtFetcher()
    try:
        fetcher.download("P04637")
    except NotImplementedError:
        assert True
    else:
        raise AssertionError("download 应抛 NotImplementedError")