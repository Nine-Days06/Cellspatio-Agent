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


class MockRScriptGenerator:
    def generate_code(self, analysis_type, params, method_context=None):
        return f"# R code for {analysis_type}"


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
        r_script_generator=MockRScriptGenerator(),
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


def test_de_analysis_uses_downloaded_asset(tmp_path):
    csv_file = tmp_path / "GSE123456_expression.csv"
    csv_file.write_text("gene,log2FC,pvalue\nTP53,1.5,0.001\n", encoding="utf-8")
    context = {'downloaded_assets': [
        {'source': 'geo', 'asset_id': 'GSE123456', 'access_path': str(csv_file)},
    ]}
    manager = _make_manager()
    result = manager.execute_workflow("对这些数据做差异表达分析", context=context)
    assert result['status'] == 'success'
    assert result['analysis_type'] == 'differential_expression'


def test_de_analysis_without_data_errors():
    manager = _make_manager()
    result = manager.execute_workflow("做差异表达分析")
    assert result['status'] == 'success'
    assert result['analysis_type'] == 'differential_expression'
    assert '演示模式' in result['message'] or '无实际数据' in result['message']


def test_de_analysis_queries_methods_kb_and_injects_context(tmp_path):
    """分析流应先查 MethodsKb，再把 context 传入 generate_code"""
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input):
            return {"type": "analysis", "analysis_type": "differential_expression",
                    "original_input": user_input}
        def extract_parameters(self, user_input):
            return {"input_files": ["counts.csv"]}

    class FakeMethods:
        def __init__(self):
            self.questions = []
        def query_context(self, q, mode="hybrid"):
            self.questions.append(q)
            return "# DESeq2\n# 不要用 TPM"

    class FakeGen:
        def __init__(self):
            self.calls = []
        def generate_code(self, analysis_type, params, method_context=None):
            self.calls.append({"type": analysis_type, "ctx": method_context})
            return "# script"

    class FakeExec:
        def execute_code(self, code):
            class R:
                returncode = 0
            return R()

    methods = FakeMethods()
    gen = FakeGen()
    wm = WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(),
        visualizer=None,
        r_script_generator=gen,
        methods_kb=methods,
    )
    result = wm.execute_workflow("做差异表达")
    assert methods.questions, "应查询方法库"
    assert gen.calls and gen.calls[0]["ctx"] and "DESeq2" in gen.calls[0]["ctx"]
    assert result["status"] == "success"


def test_de_analysis_without_methods_kb_still_works(tmp_path):
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input):
            return {"type": "analysis", "analysis_type": "differential_expression",
                    "original_input": user_input}
        def extract_parameters(self, user_input):
            return {"input_files": ["counts.csv"]}

    class FakeGen:
        def __init__(self):
            self.ctx_seen = "unset"
        def generate_code(self, analysis_type, params, method_context=None):
            self.ctx_seen = method_context
            return "# script"

    class FakeExec:
        def execute_code(self, code):
            class R:
                returncode = 0
            return R()

    gen = FakeGen()
    wm = WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(),
        visualizer=None,
        r_script_generator=gen,
        methods_kb=None,
    )
    result = wm.execute_workflow("做差异表达")
    assert result["status"] == "success"
    assert gen.ctx_seen is None