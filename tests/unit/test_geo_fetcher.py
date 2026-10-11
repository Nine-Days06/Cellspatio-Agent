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
            # 真实 API 键名为小写 gdstype/pdat（2026-04 实测）
            "gdstype": "Expression profiling by array",
            "taxon": "Homo sapiens",
            "n_samples": 12,
            "pdat": "2024-01-01",
            # 2026-10 实测补充键
            "pubmedids": ["34567890", "34567891"],
            "gpl": "23126",
            "suppfile": "CEL, CHP",
            "samples": [
                {"accession": "GSM3000001", "title": "control"},
                {"accession": "GSM3000002", "title": "treated"},
            ],
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
    assert "来源：https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123456" in text
    assert "Expression data" in text
    # 回归：键名用错会写成「类型：空」「发布日期：None」
    assert "类型：Expression profiling by array" in text
    assert "发布日期：2024-01-01" in text


def test_confirm_metadata_includes_extra_fields():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json=_esearch_response())
        return httpx.Response(200, json=_esummary_response())

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("GSE123456")
    assert info.metadata["gpl"] == "23126"
    assert info.metadata["pubmedids"] == ["34567890", "34567891"]
    assert info.metadata["suppfile"] == "CEL, CHP"
    assert info.metadata["samples"][0]["accession"] == "GSM3000001"


def test_ingest_text_includes_platform_pmids_suppfile():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json=_esearch_response())
        return httpx.Response(200, json=_esummary_response())

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("GSE123456")
    assert "平台：GPL23126" in text
    assert "关联 PMID：34567890, 34567891" in text
    assert "补充文件：CEL, CHP" in text
    # samples 仅进 metadata，不渲染进正文
    assert "GSM3000001" not in text


def test_ingest_text_skips_missing_extra_fields():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json=_esearch_response())
        payload = _esummary_response()
        entry = payload["result"]["200123456"]
        for key in ("gpl", "pubmedids", "suppfile"):
            entry.pop(key, None)
        return httpx.Response(200, json=payload)

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("GSE123456")
    assert "平台：" not in text
    assert "关联 PMID：" not in text
    assert "补充文件：" not in text


def test_local_characteristics_parses_series_matrix(tmp_path):
    import gzip

    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    asset_dir = tmp_path / "raw" / "geo" / "GSE123456"
    asset_dir.mkdir(parents=True)
    content = (
        "!Sample_characteristics_ch1\tcell line: SAS cell\tcell type: glioma\n"
        "!Sample_characteristics_ch1\tcell line: SAS cell\tage: 60\n"
        "!Sample_title\tt1\tt2\n"
    )
    with gzip.open(asset_dir / "GSE123456_series_matrix.txt.gz", "wt", encoding="utf-8") as fh:
        fh.write(content)

    fetcher = GEOFetcher(storage=storage)
    assert fetcher._local_characteristics("GSE123456") == [
        "- cell line: SAS cell",
        "- cell type: glioma",
        "- age: 60",
    ]


def test_local_characteristics_absent_returns_empty(tmp_path):
    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    # 文件缺失 → []
    assert GEOFetcher(storage=storage)._local_characteristics("GSE000001") == []
    # storage 未配置 → []
    assert GEOFetcher()._local_characteristics("GSE123456") == []


def test_ingest_text_includes_local_characteristics(tmp_path):
    import gzip

    import httpx

    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    asset_dir = tmp_path / "raw" / "geo" / "GSE123456"
    asset_dir.mkdir(parents=True)
    with gzip.open(asset_dir / "GSE123456_series_matrix.txt.gz", "wt", encoding="utf-8") as fh:
        fh.write("!Sample_characteristics_ch1\tcell line: SAS cell\tdisease: glioma\n")

    def handler(request):
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json=_esearch_response())
        return httpx.Response(200, json=_esummary_response())

    fetcher = GEOFetcher(
        storage=storage,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    text = fetcher.ingest_text("GSE123456")
    assert "样本特征：" in text
    assert "- cell line: SAS cell" in text
    assert "- disease: glioma" in text