"""CLI 入口测试"""
from unittest import mock

from src.knowledge.import_cli import build_importer, main


def test_build_importer_with_default_provider():
    with mock.patch("src.knowledge.lightrag_client.LightRAGClient"), \
         mock.patch("src.knowledge.import_cli.KnowledgeImporter") as m_import:
        result = build_importer()
        assert result is not None
        m_import.assert_called_once()


def test_main_imports_directory(tmp_path):
    fake_result = {"success": True, "total_count": 3, "imported_files": ["a.csv"]}
    with mock.patch("src.knowledge.import_cli.import_from_directory",
                    return_value=fake_result), \
         mock.patch("src.knowledge.import_cli.print"):
        rc = main(["--dir", str(tmp_path)])
        assert rc == 0


def test_main_failure_returns_nonzero(tmp_path):
    fake_result = {"success": False, "error": "boom"}
    with mock.patch("src.knowledge.import_cli.import_from_directory",
                    return_value=fake_result), \
         mock.patch("src.knowledge.import_cli.print"):
        rc = main(["--dir", str(tmp_path)])
        assert rc == 1
