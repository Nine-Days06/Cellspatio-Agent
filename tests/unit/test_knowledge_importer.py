import json
import logging
import os
import tempfile

from src.knowledge.article_text import convert_article_to_text
from src.knowledge.knowledge_importer import ImportLedger, KnowledgeImporter


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
    article = {"pmid": "12345", "title": "Test Article", "abstract": "Test abstract", "keywords": ["test", "multi-omics"], "year": 2020, "journal": "Test Journal", "doi": "10.1000/xyz"}
    text = convert_article_to_text(article)
    assert "标题：Test Article" in text
    assert "摘要：Test abstract" in text
    assert "年份：2020" in text
    assert "PMID：12345" in text
    assert "链接：https://pubmed.ncbi.nlm.nih.gov/12345/" in text
    assert "DOI：10.1000/xyz" in text


# ---------------------------------------------------------------------------
# 增量导入 + 台账（ImportLedger）
# ---------------------------------------------------------------------------

CSV_HEADER = "pmid,title,abstract,keywords,mesh_terms,authors,year,journal,doi"


def _write_csv(path, pmids: list[str]) -> None:
    """写一份 ETL 导出格式（9 列）的 CSV"""
    rows = "\n".join(
        f'{p},"Title {p}","Abstract {p}",kw,mesh,AUTHOR,2024,JOURNAL,10.1000/{p}'
        for p in pmids
    )
    path.write_text(f"{CSV_HEADER}\n{rows}\n", encoding="utf-8-sig")


def test_import_incremental_imports_only_named_new_file(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1", "2"])
    _write_csv(tmp_path / "articles_b.csv", ["3"])

    result = importer.import_incremental(str(tmp_path), files=["articles_b.csv"])

    assert result["success"] is True
    assert result["total_count"] == 1
    assert result["new_count"] == 1
    assert result["imported_files"] == [str(tmp_path / "articles_b.csv")]
    assert len(client.inserted_documents) == 1  # 目录里的历史 CSV 未被读取


def test_import_incremental_rerun_is_noop(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1", "2"])

    first = importer.import_incremental(str(tmp_path), files=["articles_a.csv"])
    second = importer.import_incremental(str(tmp_path), files=["articles_a.csv"])

    assert first["new_count"] == 2
    assert second["success"] is True
    assert second["total_count"] == 0
    assert second["new_count"] == 0
    assert second["skipped_files"] == ["articles_a.csv"]
    assert len(client.inserted_documents) == 2  # 未重复提交


def test_import_incremental_directory_scan_updates_ledger(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1", "2"])
    _write_csv(tmp_path / "articles_b.csv", ["3"])

    first = importer.import_incremental(str(tmp_path))
    second = importer.import_incremental(str(tmp_path))

    assert first["total_count"] == 3
    assert first["new_count"] == 3
    assert second["total_count"] == 0
    assert sorted(second["skipped_files"]) == ["articles_a.csv", "articles_b.csv"]

    ledger_data = json.loads((tmp_path / "import_ledger.json").read_text(encoding="utf-8"))
    assert set(ledger_data["files"]) == {"articles_a.csv", "articles_b.csv"}
    entry = ledger_data["files"]["articles_a.csv"]
    assert entry["count"] == 2
    assert len(entry["sha256"]) == 64


def test_import_incremental_ledger_file_not_treated_as_article(tmp_path):
    """目录扫描必须排除台账本身，否则会把台账 JSON 当文献导入"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1"])

    result = importer.import_incremental(str(tmp_path))

    assert result["success"] is True
    assert "import_ledger.json" not in result["imported_files"]
    assert result["total_count"] == 1


def test_import_incremental_corrupt_ledger_reimports(tmp_path):
    """台账损坏时按空台账处理（宁可重复导入也不崩溃）"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1"])
    (tmp_path / "import_ledger.json").write_text("{not json", encoding="utf-8")

    result = importer.import_incremental(str(tmp_path), files=["articles_a.csv"])

    assert result["success"] is True
    assert result["new_count"] == 1
    assert len(client.inserted_documents) == 1
    # 重导成功后台账被重写为合法 JSON
    repaired = json.loads((tmp_path / "import_ledger.json").read_text(encoding="utf-8"))
    assert "articles_a.csv" in repaired["files"]


def test_import_incremental_ledger_entry_for_deleted_csv(tmp_path):
    """台账残留已删除 CSV 的条目不影响流程"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1"])
    importer.import_incremental(str(tmp_path), files=["articles_a.csv"])
    (tmp_path / "articles_a.csv").unlink()
    _write_csv(tmp_path / "articles_b.csv", ["2"])

    result = importer.import_incremental(str(tmp_path), files=["articles_b.csv"])

    assert result["success"] is True
    assert result["new_count"] == 1
    ledger = ImportLedger(tmp_path / "import_ledger.json")
    assert ledger.is_imported("articles_a.csv")


def test_import_incremental_modified_csv_not_reimported(tmp_path, caplog):
    """策略：文件名是台账唯一键，导入后被修改的 CSV 不自动重导（仅告警）"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1"])
    importer.import_incremental(str(tmp_path), files=["articles_a.csv"])
    _write_csv(tmp_path / "articles_a.csv", ["1", "2"])

    with caplog.at_level(logging.WARNING, logger="src.knowledge.knowledge_importer"):
        result = importer.import_incremental(str(tmp_path), files=["articles_a.csv"])

    assert result["total_count"] == 0
    assert result["skipped_files"] == ["articles_a.csv"]
    assert any("修改" in r.message for r in caplog.records)


def test_import_incremental_reimport_all_ignores_ledger(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_a.csv", ["1", "2"])
    importer.import_incremental(str(tmp_path), files=["articles_a.csv"])

    result = importer.import_incremental(str(tmp_path), reimport_all=True)

    assert result["success"] is True
    assert result["total_count"] == 2  # 全量重扫确实重新提交
    assert result["new_count"] == 0    # 但不是新批次文章
    assert len(client.inserted_documents) == 4


def test_import_incremental_missing_named_file_reports_failure(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)

    result = importer.import_incremental(str(tmp_path), files=["nope.csv"])

    assert result["success"] is False
    assert "nope.csv" in result["failed_files"]


def test_ledger_corrupt_file_returns_empty(tmp_path):
    ledger_path = tmp_path / "import_ledger.json"
    ledger_path.write_text("\x00\x01broken", encoding="utf-8")

    ledger = ImportLedger(ledger_path)

    assert ledger.is_imported("anything.csv") is False
