"""CLI 入口测试"""
from unittest import mock

from src.knowledge.import_cli import build_importer, import_from_directory, main


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


def test_import_from_directory_delegates_to_incremental(tmp_path):
    """目录导入走台账增量路径，并透传 files / reimport_all"""
    fake_importer = mock.Mock()
    fake_importer.import_incremental.return_value = {
        "success": True, "total_count": 2, "new_count": 2,
        "imported_files": ["a.csv"], "skipped_files": [],
    }
    with mock.patch("src.knowledge.import_cli.build_importer",
                    return_value=fake_importer):
        result = import_from_directory(
            str(tmp_path), files=["a.csv"], reimport_all=True
        )

    fake_importer.import_incremental.assert_called_once_with(
        str(tmp_path), files=["a.csv"], reimport_all=True
    )
    assert result["new_count"] == 2


def test_main_backward_compatible_positional_args(tmp_path):
    """旧的调用方式 main(['--dir', ...]) 仍然可用，且默认非全量重导"""
    fake_result = {"success": True, "total_count": 0, "new_count": 0,
                   "imported_files": [], "skipped_files": ["a.csv"]}
    with mock.patch("src.knowledge.import_cli.import_from_directory",
                    return_value=fake_result) as m_cli, \
         mock.patch("src.knowledge.import_cli.print"):
        rc = main(["--dir", str(tmp_path)])
    assert rc == 0
    assert m_cli.call_args.kwargs["reimport_all"] is False


def test_main_reimport_all_flag(tmp_path):
    fake_result = {"success": True, "total_count": 2, "new_count": 0,
                   "imported_files": ["a.csv"], "skipped_files": []}
    with mock.patch("src.knowledge.import_cli.import_from_directory",
                    return_value=fake_result) as m_cli, \
         mock.patch("src.knowledge.import_cli.print"):
        rc = main(["--dir", str(tmp_path), "--reimport-all"])
    assert rc == 0
    assert m_cli.call_args.kwargs["reimport_all"] is True
