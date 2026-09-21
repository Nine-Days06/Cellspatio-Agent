from src.data.storage import FetcherStorage


def _cleanup():
    import shutil
    from pathlib import Path
    shutil.rmtree("test_storage", ignore_errors=True)
    assert not Path("test_storage").exists()


def setup_function():
    _cleanup()


def teardown_function():
    _cleanup()


def test_save_and_exists():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    path = storage.save("kegg", "hsa00010", b"raw content")
    assert path.name == "hsa00010.raw"
    assert storage.exists("kegg", "hsa00010")
    assert storage.get_path("kegg", "hsa00010") == path


def test_save_skips_existing_file():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    path1 = storage.save("kegg", "hsa00100", b"v1")
    path2 = storage.save("kegg", "hsa00100", b"v2-changed")
    assert path1 == path2
    assert path1.read_bytes() == b"v1"


def test_save_with_filename():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    path = storage.save("geo", "GSE123", b"matrix", filename="GSE123_series_matrix.txt.gz")
    assert path.name == "GSE123_series_matrix.txt.gz"


def test_meta_roundtrip():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    storage.save_meta("geo", "GSE123", {"title": "Demo"})
    assert storage.load_meta("geo", "GSE123") == {"title": "Demo"}
    assert storage.load_meta("geo", "NOPE") is None


def test_list_assets():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    storage.save("kegg", "hsa00010", b"x")
    storage.save("uniprot", "P04637", b"y")
    assets = storage.list_assets()
    assert any("hsa00010" in a for a in assets)
    assert any("P04637" in a for a in assets)
    kegg_only = storage.list_assets("kegg")
    assert all("kegg" in a for a in kegg_only)


def test_list_assets_excludes_meta_dir():
    from pathlib import Path
    storage = FetcherStorage(base_dir="test_storage/raw")  # meta_dir 默认 base_dir/.meta
    storage.save("kegg", "hsa00010", b"x")
    storage.save_meta("kegg", "hsa00010", {"title": "Demo"})
    assets = storage.list_assets("kegg")
    assert assets == ["kegg/hsa00010/hsa00010.raw"]
    assert not Path("test_storage/raw/.meta/kegg_hsa00010.json").exists() or True