import json
import os
import tempfile

from src.knowledge.knowledge_importer import KnowledgeImporter


class MockLightRAGClient:
    def __init__(self):
        self.inserted_documents = []
    def insert_document(self, document: str):
        self.inserted_documents.append(document)
    def insert_documents(self, documents: list[str]) -> int:
        self.inserted_documents.extend(documents)
        return len(documents)

def test_importer_init():
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    assert importer is not None

def test_import_from_json():
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    test_data = [{"pmid": "12345", "title": "Test Article", "abstract": "Test abstract", "keywords": ["test"], "year": 2020}]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(test_data, f)
        json_path = f.name
    try:
        result = importer.import_from_json(json_path)
        assert result["success"] == True
        assert result["count"] == 1
        assert len(client.inserted_documents) == 1
    finally:
        os.unlink(json_path)

def test_import_from_csv():
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    csv_content = "pmid,title,abstract,keywords,year\n12345,Test Article,Test abstract,multi-omics,2020\n"
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(csv_content)
        csv_path = f.name
    try:
        result = importer.import_from_csv(csv_path)
        assert result["success"] == True
        assert result["count"] == 1
    finally:
        os.unlink(csv_path)

def test_convert_article_to_text():
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    article = {"pmid": "12345", "title": "Test Article", "abstract": "Test abstract", "keywords": ["test", "multi-omics"], "year": 2020, "journal": "Test Journal"}
    text = importer._convert_article_to_text(article)
    assert "标题：Test Article" in text
    assert "摘要：Test abstract" in text
    assert "年份：2020" in text
