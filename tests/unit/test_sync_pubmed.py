"""sync_pubmed.py 一键同步脚本测试"""
from unittest import mock

import sync_pubmed


def test_run_etl_export_returns_latest_csv(tmp_path):
    output_dir = tmp_path / "data" / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "articles_20260921_100000.csv").write_text("a", encoding="utf-8")
    (output_dir / "articles_20260921_110000.csv").write_text("b", encoding="utf-8")

    with mock.patch("sync_pubmed.subprocess.run") as m_run:
        latest = sync_pubmed.run_etl_export(tmp_path)

    m_run.assert_called_once()
    assert latest.name == "articles_20260921_110000.csv"


def test_run_etl_export_no_csv_returns_none(tmp_path):
    with mock.patch("sync_pubmed.subprocess.run"):
        latest = sync_pubmed.run_etl_export(tmp_path)
    assert latest is None


def test_copy_to_import(tmp_path):
    src = tmp_path / "articles_20260921_120000.csv"
    src.write_text("data", encoding="utf-8")
    import_dir = tmp_path / "import"
    dest = sync_pubmed.copy_to_import(src, import_dir)
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "data"


def test_sync_and_import_full(tmp_path):
    output_dir = tmp_path / "data" / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "articles_20260921_130000.csv").write_text("x", encoding="utf-8")

    import_dir = tmp_path / "import"
    fake_import = {"success": True, "total_count": 1, "imported_files": ["articles_20260921_130000.csv"]}

    with mock.patch("sync_pubmed.subprocess.run"), \
         mock.patch("sync_pubmed.import_via_cli", return_value=fake_import) as m_cli:
        result = sync_pubmed.sync_and_import(etl_dir=tmp_path, import_dir=import_dir)

    m_cli.assert_called_once()
    assert result["imported"] == 1
    assert (import_dir / "articles_20260921_130000.csv").exists()


def test_sync_and_import_skips_import_when_requested(tmp_path):
    output_dir = tmp_path / "data" / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "articles_20260921_130001.csv").write_text("x", encoding="utf-8")

    import_dir = tmp_path / "import"
    with mock.patch("sync_pubmed.subprocess.run"), \
         mock.patch("sync_pubmed.import_via_cli") as m_cli:
        result = sync_pubmed.sync_and_import(etl_dir=tmp_path, import_dir=import_dir, do_import=False)

    m_cli.assert_not_called()
    assert result["imported"] is None
    assert result["exported"] == 1


def test_sync_and_import_no_new_data(tmp_path):
    output_dir = tmp_path / "data" / "output"
    output_dir.mkdir(parents=True)

    import_dir = tmp_path / "import"
    with mock.patch("sync_pubmed.subprocess.run"), \
         mock.patch("sync_pubmed.import_via_cli") as m_cli:
        result = sync_pubmed.sync_and_import(etl_dir=tmp_path, import_dir=import_dir)

    m_cli.assert_not_called()
    assert result["exported"] == 0
    assert result["csv"] is None


def test_main_parses_args_and_calls_sync(tmp_path):
    etl_dir = tmp_path / "etl"
    etl_dir.mkdir()
    fake = {"exported": 0, "imported": 0, "failed": 0}
    with mock.patch("sync_pubmed.sync_and_import", return_value=fake) as m_sync:
        rc = sync_pubmed.main(["--etl-dir", str(etl_dir), "--import-dir", str(tmp_path / "import")])
    assert rc == 0
    m_sync.assert_called_once()
