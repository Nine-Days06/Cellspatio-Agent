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
    test_data = [
        {
            "pmid": "12345",
            "title": "Test Article",
            "abstract": "Test abstract",
            "keywords": ["test"],
            "year": 2020,
        }
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(test_data, f)
        json_path = f.name
    try:
        result = importer.import_from_json(json_path)
        assert result["success"] is True
        assert result["count"] == 1
        assert len(client.inserted_documents) == 1
    finally:
        os.unlink(json_path)


def test_import_from_csv():
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    csv_content = (
        "pmid,title,abstract,keywords,year\n12345,Test Article,Test abstract,multi-omics,2020\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = f.name
    try:
        result = importer.import_from_csv(csv_path)
        assert result["success"] is True
        assert result["count"] == 1
    finally:
        os.unlink(csv_path)


def test_convert_article_to_text():
    article = {
        "pmid": "12345",
        "title": "Test Article",
        "abstract": "Test abstract",
        "keywords": ["test", "multi-omics"],
        "year": 2020,
        "journal": "Test Journal",
        "doi": "10.1000/xyz",
    }
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
        f'{p},"Title {p}","Abstract {p}",kw,mesh,AUTHOR,2024,JOURNAL,10.1000/{p}' for p in pmids
    )
    path.write_text(f"{CSV_HEADER}\n{rows}\n", encoding="utf-8-sig")


def test_import_incremental_imports_only_named_new_file(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_20260921_100000.csv", ["1", "2"])
    _write_csv(tmp_path / "articles_20260921_110000.csv", ["3"])

    result = importer.import_incremental(str(tmp_path), files=["articles_20260921_110000.csv"])

    assert result["success"] is True
    assert result["total_count"] == 1
    assert result["new_count"] == 1
    assert result["imported_files"] == [str(tmp_path / "articles_20260921_110000.csv")]
    assert len(client.inserted_documents) == 1  # 目录里的历史 CSV 未被读取


def test_import_incremental_rerun_is_noop(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_20260921_100000.csv", ["1", "2"])

    name = "articles_20260921_100000.csv"
    first = importer.import_incremental(str(tmp_path), files=[name])
    second = importer.import_incremental(str(tmp_path), files=[name])

    assert first["new_count"] == 2
    assert second["success"] is True
    assert second["total_count"] == 0
    assert second["new_count"] == 0
    assert second["skipped_files"] == [name]
    assert len(client.inserted_documents) == 2  # 未重复提交


def test_import_incremental_directory_scan_updates_ledger(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    first_name = "articles_20260921_100000.csv"
    second_name = "articles_20260921_110000.csv"
    _write_csv(tmp_path / first_name, ["1", "2"])
    _write_csv(tmp_path / second_name, ["3"])

    first = importer.import_incremental(str(tmp_path))
    second = importer.import_incremental(str(tmp_path))

    assert first["total_count"] == 3
    assert first["new_count"] == 3
    assert second["total_count"] == 0
    assert sorted(second["skipped_files"]) == [first_name, second_name]

    ledger_data = json.loads((tmp_path / "import_ledger.json").read_text(encoding="utf-8"))
    assert set(ledger_data["files"]) == {first_name, second_name}
    entry = ledger_data["files"][first_name]
    assert entry["count"] == 2
    assert len(entry["sha256"]) == 64


def test_import_incremental_ledger_file_not_treated_as_article(tmp_path):
    """目录扫描必须排除台账本身，否则会把台账 JSON 当文献导入"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_20260921_100000.csv", ["1"])

    result = importer.import_incremental(str(tmp_path))

    assert result["success"] is True
    assert "import_ledger.json" not in result["imported_files"]
    assert result["total_count"] == 1


def test_import_incremental_corrupt_ledger_reimports(tmp_path):
    """台账损坏时按空台账处理（宁可重复导入也不崩溃）"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    name = "articles_20260921_100000.csv"
    _write_csv(tmp_path / name, ["1"])
    (tmp_path / "import_ledger.json").write_text("{not json", encoding="utf-8")

    result = importer.import_incremental(str(tmp_path), files=[name])

    assert result["success"] is True
    assert result["new_count"] == 1
    assert len(client.inserted_documents) == 1
    # 重导成功后台账被重写为合法 JSON
    repaired = json.loads((tmp_path / "import_ledger.json").read_text(encoding="utf-8"))
    assert name in repaired["files"]


def test_import_incremental_ledger_entry_for_deleted_csv(tmp_path):
    """台账残留已删除 CSV 的条目不影响流程"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    first_name = "articles_20260921_100000.csv"
    second_name = "articles_20260921_110000.csv"
    _write_csv(tmp_path / first_name, ["1"])
    importer.import_incremental(str(tmp_path), files=[first_name])
    (tmp_path / first_name).unlink()
    _write_csv(tmp_path / second_name, ["2"])

    result = importer.import_incremental(str(tmp_path), files=[second_name])

    assert result["success"] is True
    assert result["new_count"] == 1
    ledger = ImportLedger(tmp_path / "import_ledger.json")
    assert ledger.is_imported(first_name)


def test_import_incremental_modified_csv_not_reimported(tmp_path, caplog):
    """策略：文件名是台账唯一键，导入后被修改的 CSV 不自动重导（仅告警）"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    name = "articles_20260921_100000.csv"
    _write_csv(tmp_path / name, ["1"])
    importer.import_incremental(str(tmp_path), files=[name])
    _write_csv(tmp_path / name, ["1", "2"])

    with caplog.at_level(logging.WARNING, logger="src.knowledge.knowledge_importer"):
        result = importer.import_incremental(str(tmp_path), files=[name])

    assert result["total_count"] == 0
    assert result["skipped_files"] == [name]
    assert any("修改" in r.message for r in caplog.records)


def test_import_incremental_reimport_all_ignores_ledger(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    name = "articles_20260921_100000.csv"
    _write_csv(tmp_path / name, ["1", "2"])
    importer.import_incremental(str(tmp_path), files=[name])

    result = importer.import_incremental(str(tmp_path), reimport_all=True)

    assert result["success"] is True
    assert result["total_count"] == 2  # 全量重扫确实重新提交
    assert result["new_count"] == 0  # 但不是新批次文章
    assert len(client.inserted_documents) == 4


def test_import_incremental_directory_ignores_non_contract_csv(tmp_path):
    """目录扫描只认 articles_<ts>.csv，raw 与手工 CSV 不进入导入。"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "articles_20260921_100000.csv", ["1"])
    _write_csv(tmp_path / "articles_raw_20260921_110000.csv", ["2"])
    _write_csv(tmp_path / "manual.csv", ["3"])

    result = importer.import_incremental(str(tmp_path))

    assert result["imported_files"] == [str(tmp_path / "articles_20260921_100000.csv")]
    assert result["total_count"] == 1


def test_import_incremental_rejects_missing_required_columns(tmp_path):
    """契约批次缺少必需列时在提交给 LightRAG 前失败。"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    path = tmp_path / "articles_20260921_100000.csv"
    path.write_text("pmid,title\n1,Title\n", encoding="utf-8-sig")

    result = importer.import_incremental(str(tmp_path))

    assert result["success"] is False
    assert result["failed_files"] == [path.name]
    assert client.inserted_documents == []


def test_import_incremental_rejects_explicit_non_contract_csv(tmp_path):
    """显式 files= 也不能绕过高交接契约导入手工 CSV。"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    _write_csv(tmp_path / "manual.csv", ["1"])

    result = importer.import_incremental(str(tmp_path), files=["manual.csv"])

    assert result["success"] is False
    assert result["failed_files"] == ["manual.csv"]
    assert client.inserted_documents == []


def test_import_incremental_missing_named_file_reports_failure(tmp_path):
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)

    missing = "articles_19990101_000000.csv"
    result = importer.import_incremental(str(tmp_path), files=[missing])

    assert result["success"] is False
    assert missing in result["failed_files"]


def test_ledger_corrupt_file_returns_empty(tmp_path):
    ledger_path = tmp_path / "import_ledger.json"
    ledger_path.write_text("\x00\x01broken", encoding="utf-8")

    ledger = ImportLedger(ledger_path)

    assert ledger.is_imported("anything.csv") is False


def test_import_from_csv_injects_gene_ids():
    """传入 gene_lookup 时，命中 PMID 的文档含 mention→CURIE 映射块。"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    csv_content = "pmid,title,abstract\n12345,Test Article,Test abstract\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = f.name
    try:
        importer.import_from_csv(csv_path, gene_lookup={"12345": {"TP53": "NCBIGene:7157"}})
    finally:
        os.unlink(csv_path)
    assert "基因ID：" in client.inserted_documents[0]
    assert "TP53 => NCBIGene:7157" in client.inserted_documents[0]


def test_import_from_csv_without_gene_lookup_has_no_gene_line():
    """空 lookup（或未命中）不注入基因行，其余字段照旧。"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    csv_content = "pmid,title,abstract\n12345,Test Article,Test abstract\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = f.name
    try:
        importer.import_from_csv(csv_path, gene_lookup={})
    finally:
        os.unlink(csv_path)
    assert "基因ID：" not in client.inserted_documents[0]
    assert "PMID：12345" in client.inserted_documents[0]


def test_import_from_csv_injects_despite_blank_pmid_row():
    """pmid 列含空值（整列本会 float 化）时，非空行仍能命中注入。"""
    client = MockLightRAGClient()
    importer = KnowledgeImporter(client)
    csv_content = "pmid,title,abstract\n9704485,T,A\n,T2,A2\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = f.name
    try:
        importer.import_from_csv(csv_path, gene_lookup={"9704485": {"CD34": "NCBIGene:947"}})
    finally:
        os.unlink(csv_path)
    assert "CD34 => NCBIGene:947" in client.inserted_documents[0]
