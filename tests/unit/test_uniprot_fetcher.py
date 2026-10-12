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
            # 真实 API 字段为 texts（复数），genes[].geneId 为空
            "comments": [
                {"commentType": "FUNCTION",
                 "texts": [{"value": "Acts as a tumor suppressor."}]},
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
            "genes": [{"geneName": {"value": "TP53"}, "geneId": None}],
            # 真实 API：comment 字段为 texts（复数）；GeneID 在 crossRef 而非 genes[]
            "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Tumor suppressor."}]}],
            "uniProtKBCrossReferences": [
                {"database": "GeneID", "id": "7157"},
                {"database": "HGNC", "id": "HGNC:11998"},
            ],
        })

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("P04637")
    assert "# UniProt 蛋白: P04637" in text
    # basename 必须唯一（不能是 /entry），否则 LightRAG 去重会丢文档
    assert "来源：https://www.uniprot.org/uniprotkb/P04637" in text
    assert "基因：TP53" in text
    assert "Tumor suppressor." in text
    assert "基因ID：NCBIGene:7157" in text  # 取 crossRef，非空 GeneID 才写入
    assert "NCBI GeneID：" not in text  # 统一为规范 token


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


def test_ingest_text_includes_go_annotations():
    """ingest_text 输出 GO 三类注释且排在 GeneID 行之后。"""
    def handler(request):
        return httpx.Response(200, json={
            "primaryAccession": "P04637",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "p53"}}},
            "genes": [{"geneName": {"value": "TP53"}}],
            "uniProtKBCrossReferences": [
                {"database": "GeneID", "id": "7157"},
                {"database": "GO", "id": "GO:0005634", "properties": [
                    {"key": "GoTerm", "value": "C:nucleus"},
                    {"key": "GoEvidenceType", "value": "IDA:UniProtKB"},
                ]},
                {"database": "GO", "id": "GO:0006915", "properties": [
                    {"key": "GoTerm", "value": "P:apoptotic process"},
                ]},
                {"database": "GO", "id": "GO:0003674", "properties": [
                    {"key": "GoTerm", "value": "F:molecular_function"},
                ]},
            ],
        })

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("P04637")
    assert "GO（细胞组分）: GO:0005634 nucleus [IDA:UniProtKB]" in text
    assert "GO（生物过程）: GO:0006915 apoptotic process" in text
    assert "GO（分子功能）: GO:0003674 molecular_function" in text
    # GO 行必须在 GeneID 行之后
    assert text.index("基因ID：NCBIGene:7157") < text.index("GO（细胞组分）")