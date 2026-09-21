import pytest

from src.data.fetchers.base import BaseFetcher
from src.data.registry import FetcherRegistry


class DemoFetcher(BaseFetcher):
    source = "demo"
    asset_type = "knowledge"


def test_register_and_get():
    registry = FetcherRegistry()
    fetcher = DemoFetcher()
    registry.register(fetcher)
    assert registry.has("demo")
    assert registry.get("demo") is fetcher


def test_get_unregistered_raises():
    registry = FetcherRegistry()
    with pytest.raises(KeyError):
        registry.get("missing")


def test_sources_list():
    registry = FetcherRegistry()
    registry.register(DemoFetcher())
    assert registry.sources() == ["demo"]


def test_build_default_registers_three():
    registry = FetcherRegistry.build_default()
    assert {"geo", "kegg", "uniprot"} <= set(registry.sources())