import httpx
import pytest

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher
from src.data.storage import FetcherStorage


def test_asset_meta_fields():
    meta = AssetMeta(asset_id="hsa00010", title="Glycolysis", source="kegg", asset_type="knowledge")
    assert meta.asset_id == "hsa00010"
    assert meta.asset_type == "knowledge"


def test_asset_info_defaults():
    info = AssetInfo(asset_id="P04637", title="p53", source="uniprot", asset_type="knowledge")
    assert info.description == ""
    assert info.metadata == {}


class ConcreteFetcher(BaseFetcher):
    source = "demo"
    asset_type = "knowledge"

    def search(self, query, max_results=20):
        return []

    def confirm(self, asset_id):
        return AssetInfo(asset_id=asset_id, title="demo", source="demo", asset_type="knowledge")


def test_base_abstract_methods_raise():
    fetcher = ConcreteFetcher()
    with pytest.raises(NotImplementedError):
        fetcher.download("x")
    with pytest.raises(NotImplementedError):
        fetcher.ingest_text("x")


def test_base_client_injection_and_lazy_create():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    injected = httpx.Client(transport=transport)
    fetcher = ConcreteFetcher(client=injected)
    assert fetcher._get_client() is injected

    lazy = ConcreteFetcher()
    assert isinstance(lazy._get_client(), httpx.Client)
    assert lazy._get_client() is lazy._get_client()
    assert lazy._client is not None


def test_base_save_bytes_without_storage_raises():
    fetcher = ConcreteFetcher()
    with pytest.raises(ValueError):
        fetcher._save_bytes("demo", b"data")


def test_base_save_bytes_with_storage(tmp_path):
    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    fetcher = ConcreteFetcher(storage=storage)
    path = fetcher._save_bytes("demo", b"hello", filename="a.txt")
    assert path.name == "a.txt"
    assert path.read_bytes() == b"hello"