"""Reactome ContentService fetcher 单测（httpx.MockTransport 全 mock，无真实网络）。"""

import httpx

from src.data.fetchers.reactome_fetcher import ReactomeFetcher


def _fetcher_with(handler):
    return ReactomeFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _top_pathways_payload():
    return [
        {"stId": "R-HSA-1640170", "displayName": "Cell Cycle", "schemaClass": "TopLevelPathway"},
        {"stId": "R-HSA-109582", "displayName": "Hemostasis", "schemaClass": "TopLevelPathway"},
        {"stId": "R-HSA-168256", "displayName": "Immune System", "schemaClass": "TopLevelPathway"},
    ]


def test_search_parses_top_level_pathways():
    def handler(request):
        # httpx 会把 URL 中的空格编码为 %20（实测），断言必须匹配编码后形式
        assert "/data/pathways/top/Homo%20sapiens" in str(request.url)
        return httpx.Response(200, json=_top_pathways_payload())

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("Homo sapiens")
    assert len(metas) == 3
    assert metas[0].asset_id == "R-HSA-1640170"
    assert metas[0].title == "Cell Cycle"
    assert metas[0].asset_type == "knowledge"


def test_confirm_parses_query():
    def handler(request):
        assert "/data/query/R-HSA-1640170" in str(request.url)
        return httpx.Response(
            200,
            json={
                "stId": "R-HSA-1640170",
                "displayName": "Cell Cycle",
                "speciesName": "Homo sapiens",
                "summation": [{"text": "The replication of the genome..."}],
                "goBiologicalProcess": {"displayName": "cell cycle", "accession": "0007049"},
                "isInDisease": False,
            },
        )

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("R-HSA-1640170")
    assert info.title == "Cell Cycle"
    assert "replication of the genome" in info.description
    assert info.metadata["is_in_disease"] is False


def test_ingest_text_renders_pathway():
    def handler(request):
        if "/containedEvents" in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {
                        "stId": "R-HSA-69620",
                        "displayName": "Cell Cycle Checkpoints",
                        "className": "Pathway",
                    },
                    {
                        "stId": "R-HSA-69615",
                        "displayName": "G1/S DNA Damage Checkpoints",
                        "className": "Pathway",
                    },
                ],
            )
        return httpx.Response(
            200,
            json={
                "stId": "R-HSA-1640170",
                "displayName": "Cell Cycle",
                "speciesName": "Homo sapiens",
                "summation": [{"text": "The replication of the genome."}],
                "goBiologicalProcess": {"displayName": "cell cycle", "accession": "0007049"},
                "isInDisease": False,
            },
        )

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("R-HSA-1640170")
    assert text.startswith("# Reactome 通路: R-HSA-1640170")
    assert "名称：Cell Cycle" in text
    assert "物种：Homo sapiens" in text
    assert "GO 生物过程：cell cycle (GO:0007049)" in text
    assert "摘要：The replication of the genome." in text
    assert (
        "子事件（2）: R-HSA-69620 Cell Cycle Checkpoints; R-HSA-69615 G1/S DNA Damage Checkpoints"
        in text
    )


def test_ingest_text_caps_contained_events():
    """containedEvents 超过 50 条必须封顶（实测 R-HSA-1640170 有 597 条），
    且实测部分通路返回 dict 与 int（裸 dbId）混合列表（R-HSA-109582: 6/407），
    int 元素必须被跳过。"""

    def handler(request):
        if "/containedEvents" in str(request.url):
            events = (
                [418346]
                + [
                    {"stId": f"R-HSA-{i:06d}", "displayName": f"Event {i}", "className": "Pathway"}
                    for i in range(600)
                ]
                + [75892]
            )
            return httpx.Response(200, json=events)
        return httpx.Response(
            200,
            json={
                "stId": "R-HSA-1640170",
                "displayName": "Cell Cycle",
                "speciesName": "Homo sapiens",
                "summation": [],
                "goBiologicalProcess": None,
                "isInDisease": False,
            },
        )

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("R-HSA-1640170")
    assert "子事件（50）" in text  # 封顶标签为 50
    assert "…等 600 条" in text  # 总数为 600（2 个 int 已过滤）
    assert "418346" not in text  # 裸 dbId 不渲染
    assert "75892" not in text
    assert "摘要：无" in text  # summation 为空 → 兼容
