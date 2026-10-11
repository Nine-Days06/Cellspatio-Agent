from src.knowledge.knowledge_builder import KnowledgeBuilder


class MockLightRAGClient:
    def __init__(self):
        self.inserted_documents = []
    def insert_documents(self, documents) -> int:
        self.inserted_documents.extend(documents)
        return len(documents)


class FakeFetcher:
    def __init__(self, source):
        self.source = source
        self.calls = []
    def ingest_text(self, asset_id) -> str:
        self.calls.append(asset_id)
        return f"# text of {self.source}:{asset_id}"


def test_build_from_kegg_batch():
    fetcher = FakeFetcher("kegg")
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_kegg(["hsa00010", "hsa00020"], fetcher=fetcher)
    assert result["inserted"] == 2
    assert fetcher.calls == ["hsa00010", "hsa00020"]


def test_build_from_uniprot_batch():
    fetcher = FakeFetcher("uniprot")
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_uniprot(["P04637"], fetcher=fetcher)
    assert result["inserted"] == 1
    assert "P04637" in fetcher.calls


def test_build_from_geo_metadata():
    fetcher = FakeFetcher("geo")
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_geo_metadata(["GSE123456"], fetcher=fetcher)
    assert result["inserted"] == 1


def test_build_from_articles_batch():
    client = MockLightRAGClient()
    builder = KnowledgeBuilder(client)
    articles = [
        {"pmid": "1", "title": "A", "abstract": "abs"},
        {"pmid": "2", "title": "B", "abstract": "abs"},
    ]
    result = builder.build_from_articles(articles)
    assert result == {"inserted": 2}
    assert len(client.inserted_documents) == 2


def test_build_initial_dispatch_sources(monkeypatch):
    client = MockLightRAGClient()
    builder = KnowledgeBuilder(client)

    fetchers = {"kegg": FakeFetcher("kegg"), "uniprot": FakeFetcher("uniprot")}

    class FakeRegistry:
        def get(self, source):
            return fetchers[source]

    monkeypatch.setattr(builder, "registry", FakeRegistry())
    config = {
        "sources": [
            {"type": "kegg", "pathway_ids": ["hsa00010"]},
            {"type": "uniprot", "accessions": ["P04637"]},
        ]
    }
    result = builder.build_initial_knowledge_base(config)
    assert result["inserted"] == 2
    assert result["sources"] == ["kegg", "uniprot"]


def test_build_from_pubmed_deprecated():
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_pubmed(["multi-omics"])
    assert result["inserted"] == 0


def test_build_from_cellmarker(monkeypatch):
    from pathlib import Path

    import src.knowledge.curated_sources as cs

    seen = {}

    def fake_texts(xlsx_path=None):
        seen["path"] = xlsx_path
        return ["# CellMarker 细胞类型:\n## B cell"]

    monkeypatch.setattr(cs, "cellmarker_texts", fake_texts)
    client = MockLightRAGClient()
    builder = KnowledgeBuilder(client)
    result = builder.build_from_cellmarker("/tmp/marker.xlsx")
    assert result == {"requested": 1, "inserted": 1}
    assert seen["path"] == Path("/tmp/marker.xlsx")
    assert client.inserted_documents[0].startswith("# CellMarker 细胞类型:")


def test_build_from_cell_ontology_default_path_is_none(monkeypatch):
    import src.knowledge.curated_sources as cs

    seen = {}

    def fake_texts(obo_path=None):
        seen["path"] = obo_path
        return ["# Cell Ontology 术语:\nCL:0000084 T cell", "# Cell Ontology 术语:\nCL:0000085 B cell"]

    monkeypatch.setattr(cs, "cell_ontology_texts", fake_texts)
    client = MockLightRAGClient()
    builder = KnowledgeBuilder(client)
    result = builder.build_from_cell_ontology()
    assert result == {"requested": 2, "inserted": 2}
    assert seen["path"] is None  # 不传路径 → 交给 ensure_file 下载/缓存