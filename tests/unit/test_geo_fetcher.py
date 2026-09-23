import httpx

from src.data.fetchers.geo_fetcher import GEOFetcher


def _fetcher_with(handler):
    return GEOFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _esearch_response(ids=None):
    return {"esearchresult": {"idlist": ids or ["200123456"]}}


def _esummary_response():
    return {"result": {
        "uids": ["200123456"],
        "200123456": {
            "accession": "GSE123456",
            "title": "Expression data from diabetes patients",
            "summary": "A summary of the dataset.",
            "gdsType": "Expression profiling by array",
            "taxon": "Homo sapiens",
            "n_samples": 12,
            "PDAT": "2024-01-01",
        },
    }}


def test_search_esearch_then_esummary():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            assert "db=gds" in str(request.url)
            return httpx.Response(200, json=_esearch_response())
        if "esummary.fcgi" in str(request.url):
            return httpx.Response(200, json=_esummary_response())
        return httpx.Response(404)

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("diabetes")
    assert len(metas) == 1
    assert metas[0].asset_id == "GSE123456"
    assert metas[0].asset_type == "analysis"
    assert "diabetes" in metas[0].title


def test_search_empty_idlist():
    def handler(request):
        return httpx.Response(200, json=_esearch_response(ids=[]))

    fetcher = _fetcher_with(handler)
    assert fetcher.search("nothing_matches") == []


def test_confirm_uses_accession_term():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            assert request.url.params.get("term") == "GSE123456[ACCN]"
            return httpx.Response(200, json=_esearch_response())
        if "esummary.fcgi" in str(request.url):
            return httpx.Response(200, json=_esummary_response())
        return httpx.Response(404)

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("GSE123456")
    assert info.title == "Expression data from diabetes patients"
    assert info.metadata["taxon"] == "Homo sapiens"


def test_series_matrix_url_rules():
    # NCBI 规则：accession 数字末 3 位换成 nnn（官方 geo_paccess.html）
    fetcher = GEOFetcher()
    assert fetcher._series_matrix_url("GSE123456") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE123nnn/GSE123456/matrix/GSE123456_series_matrix.txt.gz"
    )
    assert fetcher._series_matrix_url("GSE48351") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE48nnn/GSE48351/matrix/GSE48351_series_matrix.txt.gz"
    )
    assert fetcher._series_matrix_url("GSE1234") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE1nnn/GSE1234/matrix/GSE1234_series_matrix.txt.gz"
    )
    assert fetcher._series_matrix_url("GSE12") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSEnnn/GSE12/matrix/GSE12_series_matrix.txt.gz"
    )


def test_download_fetches_and_saves(tmp_path):
    def handler(request):
        url = str(request.url)
        assert "ftp.ncbi.nlm.nih.gov/geo/series" in url
        return httpx.Response(200, content=b"matrix-content")

    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    fetcher = GEOFetcher(storage=storage, client=httpx.Client(transport=httpx.MockTransport(handler)))
    path = fetcher.download("GSE123456")
    assert path.name == "GSE123456_series_matrix.txt.gz"
    assert path.read_bytes() == b"matrix-content"


def test_download_uses_cache_when_exists(tmp_path):
    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=b"data")

    fetcher = GEOFetcher(
        storage=storage,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    fetcher.download("GSE1")
    fetcher.download("GSE1")
    assert calls["n"] == 1  # 第二次命中缓存不再请求


def test_download_404_raises(tmp_path):
    def handler(request):
        return httpx.Response(404)

    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    fetcher = GEOFetcher(storage=storage, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        fetcher.download("GSE999999")
    except ValueError:
        assert True
    else:
        raise AssertionError("download 应抛 ValueError")


def test_ingest_text_from_summary():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json=_esearch_response())
        return httpx.Response(200, json=_esummary_response())

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("GSE123456")
    assert "# GEO 数据集: GSE123456" in text
    assert "Expression data" in text