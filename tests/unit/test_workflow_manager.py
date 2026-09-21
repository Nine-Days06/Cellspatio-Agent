from pathlib import Path

from src.control.intent_parser import IntentParser
from src.control.workflow_manager import WorkflowManager
from src.data.fetchers.base import AssetInfo, AssetMeta


class MockKnowledgeClient:
    def query(self, query): return "mock"


class MockRExecutor:
    def execute_code(self, code):
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class MockVisualizer:
    pass


class FakeFetcher:
    source = "geo"
    asset_type = "analysis"

    def __init__(self, metas=None):
        if metas is None:
            self.metas = [
                AssetMeta(asset_id="GSE123456", title="RNA-seq of HCC",
                          source="geo", asset_type="analysis"),
                AssetMeta(asset_id="GSE654321", title="ATAC-seq of HCC",
                          source="geo", asset_type="analysis"),
            ]
        else:
            self.metas = metas

    def search(self, query, max_results=20):
        return self.metas[:max_results]

    def confirm(self, asset_id):
        return AssetInfo(asset_id=asset_id, title="RNA-seq of HCC",
                         source="geo", asset_type="analysis",
                         metadata={"organism": "Homo sapiens"})

    def download(self, asset_id):
        return Path("data/raw/geo") / asset_id / "series_matrix.txt"

    def ingest_text(self, asset_id):
        return f"GEO 数据集 {asset_id} 的元数据文本"


class FakeRegistry:
    def __init__(self, fetchers=None):
        self._f = fetchers or {"geo": FakeFetcher()}
        self._fetchers = list(self._f.keys())

    def get(self, source):
        return self._f[source]

    def sources(self):
        return self._fetchers


class FakeBuilder:
    def __init__(self):
        self.received = []
    def build_from_text(self, text):
        self.received.append(text)
        return {"inserted": 1}


def _make_manager(fetcher_registry=FakeRegistry(), builder=None):
    return WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=MockKnowledgeClient(),
        r_executor=MockRExecutor(),
        visualizer=MockVisualizer(),
        fetcher_registry=fetcher_registry,
        knowledge_builder=builder or FakeBuilder(),
    )


def test_fetch_data_workflow_needs_confirmation():
    manager = _make_manager()
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result['type'] == 'fetch_data'
    assert result['status'] == 'needs_confirmation'
    assert result['candidates'][0]['asset_id'] == 'GSE123456'


def test_fetch_data_no_registry():
    manager = _make_manager(fetcher_registry=None)
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result['status'] == 'error'


def test_fetch_data_no_results():
    manager = _make_manager(FakeRegistry({"geo": FakeFetcher(metas=[])}))
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result['status'] == 'no_results'


def test_confirm_and_download_success():
    manager = _make_manager()
    result = manager.confirm_and_download("geo", "GSE123456")
    assert result['status'] == 'success'
    assert result['type'] == 'fetch_result'
    assert result['asset']['asset_id'] == 'GSE123456'
    assert str(result['asset']['access_path']).endswith("series_matrix.txt")


def test_ingest_asset_to_kb():
    builder = FakeBuilder()
    manager = _make_manager(builder=builder)
    result = manager.ingest_asset_to_kb("geo", "GSE123456")
    assert result['status'] == 'success'
    assert result['inserted'] == 1
    assert "GSE123456" in builder.received[0]


def test_fetch_data_context_records_candidates():
    manager = _make_manager()
    context = {}
    manager.execute_workflow("帮我下载 GEO 数据集", context=context)
    assert len(context['fetch_candidates']) == 2