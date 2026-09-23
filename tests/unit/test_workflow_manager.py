from pathlib import Path

from src.control.intent_parser import IntentParser
from src.control.workflow_manager import WorkflowManager
from src.data.fetchers.base import AssetInfo, AssetMeta


class MockKnowledgeClient:
    def query(self, query):
        return "mock"


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
                AssetMeta(
                    asset_id="GSE123456",
                    title="RNA-seq of HCC",
                    source="geo",
                    asset_type="analysis",
                ),
                AssetMeta(
                    asset_id="GSE654321",
                    title="ATAC-seq of HCC",
                    source="geo",
                    asset_type="analysis",
                ),
            ]
        else:
            self.metas = metas

    def search(self, query, max_results=20):
        return self.metas[:max_results]

    def confirm(self, asset_id):
        return AssetInfo(
            asset_id=asset_id,
            title="RNA-seq of HCC",
            source="geo",
            asset_type="analysis",
            metadata={"organism": "Homo sapiens"},
        )

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


# 模块级默认注册表：避免 B008（默认参数中调用构造函数），同时保留显式传 None 的语义
_DEFAULT_REGISTRY = FakeRegistry()


def _make_manager(fetcher_registry=_DEFAULT_REGISTRY, builder=None):
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
    assert result["type"] == "fetch_data"
    assert result["status"] == "needs_confirmation"
    assert result["candidates"][0]["asset_id"] == "GSE123456"


def test_fetch_data_no_registry():
    manager = _make_manager(fetcher_registry=None)
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result["status"] == "error"


def test_fetch_data_no_results():
    manager = _make_manager(FakeRegistry({"geo": FakeFetcher(metas=[])}))
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result["status"] == "no_results"


def test_confirm_and_download_success():
    manager = _make_manager()
    result = manager.confirm_and_download("geo", "GSE123456")
    assert result["status"] == "success"
    assert result["type"] == "fetch_result"
    assert result["asset"]["asset_id"] == "GSE123456"
    assert str(result["asset"]["access_path"]).endswith("series_matrix.txt")


def test_ingest_asset_to_kb():
    builder = FakeBuilder()
    manager = _make_manager(builder=builder)
    result = manager.ingest_asset_to_kb("geo", "GSE123456")
    assert result["status"] == "success"
    assert result["inserted"] == 1
    assert "GSE123456" in builder.received[0]


def test_fetch_data_context_records_candidates():
    manager = _make_manager()
    context = {}
    manager.execute_workflow("帮我下载 GEO 数据集", context=context)
    assert len(context["fetch_candidates"]) == 2


def test_de_analysis_uses_downloaded_asset(tmp_path):
    csv_file = tmp_path / "GSE123456_expression.csv"
    csv_file.write_text("gene,log2FC,pvalue\nTP53,1.5,0.001\n", encoding="utf-8")
    context = {
        "downloaded_assets": [
            {"source": "geo", "asset_id": "GSE123456", "access_path": str(csv_file)},
        ]
    }
    manager = _make_manager()
    result = manager.execute_workflow("对这些数据做差异表达分析", context=context)
    assert result["status"] == "success"
    assert result["analysis_type"] == "differential_expression"


def test_de_analysis_without_data_errors():
    manager = _make_manager()
    result = manager.execute_workflow("做差异表达分析")
    assert result["status"] == "success"
    assert result["analysis_type"] == "differential_expression"
    assert "演示模式" in result["message"] or "无实际数据" in result["message"]


def test_de_analysis_queries_methods_kb_and_injects_context():
    """分析流应先查 MethodsKb，再把 context 传入 generate_code"""
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {
                "type": "analysis",
                "analysis_type": "differential_expression",
                "original_input": user_input,
            }

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


def test_de_analysis_without_methods_kb_still_works():
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {
                "type": "analysis",
                "analysis_type": "differential_expression",
                "original_input": user_input,
            }

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


def test_de_success_writes_summary_to_knowledge_builder():
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {"type": "analysis", "analysis_type": "differential_expression",
                    "original_input": user_input}
        def extract_parameters(self, user_input):
            return {"input_files": ["counts.csv"]}

    class FakeKBBuilder:
        def __init__(self):
            self.texts = []
        def build_from_text(self, text, metadata=None):
            self.texts.append(text)
            return {"inserted": 1}

    class FakeGen:
        def generate_code(self, analysis_type, params, method_context=None):
            return "# s"

    class FakeExec:
        def execute_code(self, code):
            class R:
                returncode = 0
            return R()

    builder = FakeKBBuilder()
    wm = WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(),
        visualizer=None,
        r_script_generator=FakeGen(),
        knowledge_builder=builder,
    )
    result = wm.execute_workflow("做差异表达")
    assert result["status"] == "success"
    assert builder.texts, "成功后应写回知识库"
    assert "差异表达" in builder.texts[0]


def test_de_failure_does_not_write_knowledge():
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {"type": "analysis", "analysis_type": "differential_expression",
                    "original_input": user_input}
        def extract_parameters(self, user_input):
            return {"input_files": ["counts.csv"]}

    class FakeKBBuilder:
        def __init__(self):
            self.texts = []
        def build_from_text(self, text, metadata=None):
            self.texts.append(text)
            return {"inserted": 1}

    class FakeGen:
        def generate_code(self, analysis_type, params, method_context=None):
            return "# s"

    class FakeExec:
        def execute_code(self, code):
            raise RuntimeError("R boom")

    builder = FakeKBBuilder()
    wm = WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(),
        visualizer=None,
        r_script_generator=FakeGen(),
        knowledge_builder=builder,
    )
    try:
        wm.execute_workflow("做差异表达")
    except RuntimeError:
        pass
    assert builder.texts == []


def test_de_success_returns_explanation_field():
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {"type": "analysis", "analysis_type": "differential_expression",
                    "original_input": user_input}
        def extract_parameters(self, user_input):
            return {"input_files": ["counts.csv"]}

    class FakeExplainer:
        def generate_llm_explanation(self, data, question):
            return "解说文本"

    class FakeGen:
        def generate_code(self, analysis_type, params, method_context=None):
            return "# s"

    class FakeExec:
        def execute_code(self, code):
            class R:
                returncode = 0
            return R()

    wm = WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(),
        visualizer=None,
        r_script_generator=FakeGen(),
        explainer=FakeExplainer(),
    )
    result = wm.execute_workflow("做差异表达")
    assert result.get("explanation") == "解说文本"


def test_fetch_candidates_include_reason_and_confirm_detail():
    from src.control.workflow_manager import WorkflowManager

    class Meta:
        def __init__(self, aid, title):
            self.asset_id = aid
            self.title = title
            self.source = "geo"
            self.asset_type = "analysis"

    class Info:
        def __init__(self):
            self.title = "GSE1 detail"
            self.description = "肝细胞癌 RNA-seq，n=10"
            self.metadata = {"organism": "Homo sapiens", "samples": 10}
            self.asset_id = "GSE1"
            self.source = "geo"
            self.asset_type = "analysis"

    class FakeFetcher:
        source = "geo"

        def search(self, query, max_results=5):
            return [Meta("GSE1", "HCC RNA-seq")]

        def confirm(self, asset_id):
            return Info()

    class FakeRegistry:
        def sources(self):
            return ["geo"]

        def get(self, source):
            return FakeFetcher()

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {"type": "fetch_data", "original_input": user_input}

        def extract_parameters(self, user_input):
            return {}

    wm = WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=None,
        visualizer=None,
        fetcher_registry=FakeRegistry(),
    )
    result = wm.execute_workflow("帮我找 肝癌 RNA-seq 数据集")
    cands = result["candidates"]
    assert cands[0].get("reason")
    assert cands[0].get("description")
    assert cands[0].get("metadata", {}).get("organism")


def test_confirm_and_download_appends_lineage(tmp_path, monkeypatch):
    from src.control.workflow_manager import WorkflowManager
    from src.data import lineage as lineage_mod

    lineage_path = tmp_path / "lineage.jsonl"

    class Info:
        title = "t"
        description = "d"
        metadata = {}
        asset_id = "GSE1"
        source = "geo"
        asset_type = "analysis"

    class FakeFetcher:
        source = "geo"

        def confirm(self, asset_id):
            return Info()

        def download(self, asset_id):
            return tmp_path / "file.txt"

    class FakeRegistry:
        def get(self, source):
            return FakeFetcher()

    wm = WorkflowManager(
        intent_parser=None,
        knowledge_client=None,
        r_executor=None,
        visualizer=None,
        fetcher_registry=FakeRegistry(),
        lineage_path=str(lineage_path),
    )
    wm.confirm_and_download("geo", "GSE1")
    rows = lineage_mod.read_lineage(lineage_path)
    assert len(rows) == 1
    assert rows[0]["asset_id"] == "GSE1"


def test_de_retries_after_repair_then_succeeds():
    from src.analysis.r_executor import RExecutorError
    from src.control.workflow_manager import WorkflowManager

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {"type": "analysis", "analysis_type": "differential_expression",
                    "original_input": user_input}
        def extract_parameters(self, user_input):
            return {"input_files": ["counts.csv"]}

    class FakeGen:
        def generate_code(self, analysis_type, params, method_context=None):
            return "# attempt"

    class FlakyExec:
        def __init__(self):
            self.n = 0
        def execute_code(self, code):
            self.n += 1
            if self.n == 1:
                raise RExecutorError("first fail")
            class R:
                returncode = 0
            return R()

    class FakeRepairer:
        def __init__(self):
            self.calls = []
        def repair(self, code, err):
            self.calls.append(err)
            return "# repaired"

    rep = FakeRepairer()
    wm = WorkflowManager(
        intent_parser=FakeIntent(), knowledge_client=None,
        r_executor=FlakyExec(), visualizer=None,
        r_script_generator=FakeGen(),
        code_repairer=rep, max_repair_attempts=2,
    )
    result = wm.execute_workflow("做差异表达")
    assert result["status"] == "success"
    assert rep.calls and "first fail" in rep.calls[0]
    assert result.get("repair_count") == 1


# ---------------------------------------------------------------------------
# 按需补库（lazy ingest）
# ---------------------------------------------------------------------------

class MockKnowledgeClientWithContext:
    """带 query_context 的知识客户端；context_len 控制 miss-check 是否触发"""

    def __init__(self, context_len: int = 0):
        self.context_len = context_len
        self.context_calls = []
        self.query_calls = []

    def query(self, query):
        self.query_calls.append(query)
        return "mock-answer"

    def query_context(self, query, mode="hybrid"):
        self.context_calls.append(query)
        return "x" * self.context_len


class FakeBioFetcher:
    """KEGG/UniProt 风格 fetcher；search/ingest 可注入失败"""

    def __init__(self, source, metas=None, fail_ids=None):
        self.source = source
        self.asset_type = "knowledge"
        self.metas = metas if metas is not None else []
        self.fail_ids = set(fail_ids or [])
        self.ingested = []
        self.search_calls = []

    def search(self, query, max_results=20):
        self.search_calls.append(query)
        return self.metas[:max_results]

    def confirm(self, asset_id):
        from types import SimpleNamespace

        return SimpleNamespace(
            asset_id=asset_id,
            title=asset_id,
            source=self.source,
            asset_type="knowledge",
            description="",
            metadata={},
        )

    def download(self, asset_id):
        return Path("data/raw") / self.source / asset_id

    def ingest_text(self, asset_id):
        if asset_id in self.fail_ids:
            raise RuntimeError(f"ingest {asset_id} boom")
        self.ingested.append(asset_id)
        return f"{self.source} text for {asset_id}"


class FakeBioRegistry:
    def __init__(self, fetchers):
        self._f = dict(fetchers)

    def get(self, source):
        return self._f[source]

    def sources(self):
        return list(self._f.keys())

    def has(self, source):
        return source in self._f


class KnowledgeIntent:
    """强制路由到 knowledge_query，避免依赖关键词回退；参数抽取复用 IntentParser"""

    def __init__(self):
        self._params = IntentParser()

    def parse(self, user_input, context=None):
        return {
            "type": "knowledge_query",
            "confidence": 1.0,
            "original_input": user_input,
            "analysis_type": None,
            "secondary_intent": None,
            "clarification": None,
        }

    def extract_parameters(self, user_input):
        return self._params.extract_parameters(user_input)


def _meta(source, asset_id, title=None):
    return AssetMeta(
        asset_id=asset_id,
        title=title or asset_id,
        source=source,
        asset_type="knowledge",
    )


def _make_lazy_manager(
    context_len=0,
    registry=None,
    builder=None,
    knowledge_client=None,
    intent_parser=None,
):
    client = knowledge_client or MockKnowledgeClientWithContext(context_len)
    return WorkflowManager(
        intent_parser=intent_parser or KnowledgeIntent(),
        knowledge_client=client,
        r_executor=MockRExecutor(),
        visualizer=MockVisualizer(),
        fetcher_registry=registry,
        knowledge_builder=builder if builder is not None else FakeBuilder(),
        r_script_generator=MockRScriptGenerator(),
    ), client


def test_lazy_ingest_triggers_on_short_context_with_explicit_kegg_id():
    """context < 300 且问题含 map04115 → 自动 kegg 入库，再 query"""
    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    builder = FakeBuilder()
    wm, client = _make_lazy_manager(context_len=50, registry=registry, builder=builder)

    result = wm.execute_workflow("p53 通路 map04115 是什么")

    assert result["status"] == "success"
    assert result["lazy_ingested"] == [
        {"source": "kegg", "asset_id": "map04115", "title": "map04115"}
    ]
    assert kegg.ingested == ["map04115"]
    assert builder.received, "入库文本应写入 knowledge_builder"
    # 先补库再正式回答
    assert client.query_calls == ["p53 通路 map04115 是什么"]


def test_lazy_ingest_skips_when_context_long_enough():
    """context >= 300 → 不补库，直接 query"""
    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    wm, client = _make_lazy_manager(
        context_len=300, registry=registry, builder=FakeBuilder()
    )

    result = wm.execute_workflow("p53 通路 map04115 是什么")

    assert result["lazy_ingested"] == []
    assert kegg.ingested == []
    assert client.context_calls  # 仍做了 miss-check


def test_lazy_ingest_skipped_without_query_context():
    """knowledge_client 无 query_context → 跳过补库，仍正常回答"""
    class ClientNoCtx:
        def __init__(self):
            self.query_calls = []

        def query(self, query):
            self.query_calls.append(query)
            return "ok\n\n### References\n- [1] x\n"

    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    client = ClientNoCtx()
    wm, _ = _make_lazy_manager(
        context_len=0, registry=registry, builder=FakeBuilder(), knowledge_client=client
    )

    result = wm.execute_workflow("map04115 是什么")

    assert result["lazy_ingested"] == []
    assert kegg.ingested == []
    # 剥离 LLM 自造 References 段
    assert result["response"] == "ok\n"
    assert result["references"] == []


def test_knowledge_workflow_uses_query_with_references():
    """有 query_with_references → 结构化引用进 result，response 仍为 str"""
    class StructClient:
        def query(self, query):
            raise AssertionError("should use query_with_references")

        def query_with_references(self, query):
            return {
                "response": "正文\n",
                "references": [{"reference_id": "1", "file_path": "https://x/y"}],
            }

    wm, _ = _make_lazy_manager(
        context_len=999, registry=None, builder=FakeBuilder(),
        knowledge_client=StructClient(),
    )
    result = wm.execute_workflow("TP53 是什么")
    assert result["response"] == "正文\n"
    assert result["references"] == [
        {"reference_id": "1", "file_path": "https://x/y"}
    ]


def test_lazy_ingest_query_context_error_does_not_block_answer():
    """query_context 抛错 → 记警告、跳过补库，query 仍执行"""
    class CtxBoom:
        def query(self, query):
            return "answered"

        def query_context(self, query, mode="hybrid"):
            raise RuntimeError("ctx boom")

    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    wm, _ = _make_lazy_manager(
        context_len=0, registry=registry, builder=FakeBuilder(), knowledge_client=CtxBoom()
    )

    result = wm.execute_workflow("map04115 是什么")

    assert result["status"] == "success"
    assert result["lazy_ingested"] == []
    assert result["response"] == "answered"


def test_lazy_ingest_requires_builder_and_registry():
    """缺 builder 或 registry → 返回空列表，不报错"""
    wm_missing_builder, _ = _make_lazy_manager(
        context_len=0,
        registry=FakeBioRegistry({"kegg": FakeBioFetcher("kegg")}),
        builder=None,  # _make_lazy_manager 会填 FakeBuilder；显式覆盖
    )
    # 直接置空模拟未配置
    wm_missing_builder.knowledge_builder = None
    assert wm_missing_builder._lazy_ingest_missing("map04115 是什么", {}) == []

    wm_missing_registry, _ = _make_lazy_manager(context_len=0, registry=None, builder=FakeBuilder())
    assert wm_missing_registry._lazy_ingest_missing("map04115 是什么", {}) == []


def test_lazy_ingest_uniprot_accession_and_dedup():
    """P04637 识别为 uniprot；重复 accession 只入库一次"""
    uni = FakeBioFetcher("uniprot")
    registry = FakeBioRegistry({"uniprot": uni})
    builder = FakeBuilder()
    wm, _ = _make_lazy_manager(context_len=10, registry=registry, builder=builder)

    result = wm.execute_workflow("P04637 和 P04637 的功能是什么")

    assert result["lazy_ingested"] == [
        {"source": "uniprot", "asset_id": "P04637", "title": "P04637"}
    ]
    assert uni.ingested == ["P04637"]


def test_lazy_ingest_max_assets_cap():
    """显式 ID 超过上限 → 只入库 LAZY_INGEST_MAX_ASSETS 条"""
    from src.control.workflow_manager import LAZY_INGEST_MAX_ASSETS

    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    wm, _ = _make_lazy_manager(context_len=0, registry=registry, builder=FakeBuilder())

    # 5 个合法 KEGG map ID
    query = "map00010 map00020 map00030 map00040 map00050 分别是什么"
    result = wm.execute_workflow(query)

    assert len(result["lazy_ingested"]) == LAZY_INGEST_MAX_ASSETS == 3
    assert len(kegg.ingested) == 3
    assert kegg.ingested == ["map00010", "map00020", "map00030"]


def test_lazy_ingest_search_fallback_when_no_explicit_id():
    """无显式 ID → search 兜底（gene: 前缀优先 uniprot）"""
    kegg = FakeBioFetcher(
        "kegg", metas=[_meta("kegg", "map04115", "p53 signaling pathway")]
    )
    uni = FakeBioFetcher(
        "uniprot", metas=[_meta("uniprot", "P04637", "Cellular tumor antigen p53")]
    )
    registry = FakeBioRegistry({"kegg": kegg, "uniprot": uni})
    wm, _ = _make_lazy_manager(context_len=20, registry=registry, builder=FakeBuilder())

    result = wm.execute_workflow("TP53 在癌症中的作用是什么")

    ids = {(item["source"], item["asset_id"]) for item in result["lazy_ingested"]}
    assert ("kegg", "map04115") in ids
    assert ("uniprot", "P04637") in ids
    # uniprot search 应使用 gene: 前缀（IntentParser 抽出 TP53）
    assert any(q.startswith("gene:") for q in uni.search_calls)


def test_lazy_ingest_excludes_geo_even_if_in_registry():
    """范围仅 kegg/uniprot：即使 geo 在注册表也不补 GEO"""
    geo = FakeBioFetcher(
        "geo",
        metas=[_meta("geo", "GSE123456", "HCC RNA-seq")],
        fail_ids=set(),
    )
    kegg = FakeBioFetcher("kegg", metas=[_meta("kegg", "map04115")])
    registry = FakeBioRegistry({"geo": geo, "kegg": kegg})
    wm, _ = _make_lazy_manager(context_len=0, registry=registry, builder=FakeBuilder())

    result = wm.execute_workflow("GSE123456 是什么数据集")

    sources = {item["source"] for item in result["lazy_ingested"]}
    assert "geo" not in sources
    assert geo.ingested == []
    # GSE 不匹配 KEGG/UniProt 显式正则；search 仍只走 kegg/uniprot
    assert all(src in ("kegg", "uniprot") for src in sources)


def test_lazy_ingest_single_failure_continues():
    """单条 ingest 失败 → 记日志，后续条目继续"""
    kegg = FakeBioFetcher(
        "kegg",
        metas=[
            _meta("kegg", "map00010"),
            _meta("kegg", "map00020"),
        ],
        fail_ids={"map00010"},
    )
    registry = FakeBioRegistry({"kegg": kegg})
    wm, _ = _make_lazy_manager(context_len=0, registry=registry, builder=FakeBuilder())

    # 无显式 ID → search 返回两条，第一条失败
    result = wm.execute_workflow("糖酵解通路是什么")

    assert result["lazy_ingested"] == [
        {"source": "kegg", "asset_id": "map00020", "title": "map00020"}
    ]
    assert kegg.ingested == ["map00020"]


def test_lazy_ingest_not_called_for_analysis_intent():
    """分析意图不走 knowledge_workflow，不触发补库"""
    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    builder = FakeBuilder()
    wm = WorkflowManager(
        intent_parser=IntentParser(),  # 关键词回退：差异表达 → analysis
        knowledge_client=MockKnowledgeClientWithContext(0),
        r_executor=MockRExecutor(),
        visualizer=MockVisualizer(),
        fetcher_registry=registry,
        knowledge_builder=builder,
        r_script_generator=MockRScriptGenerator(),
    )

    result = wm.execute_workflow("对 map00010 做差异表达分析")

    assert result.get("analysis_type") == "differential_expression"
    assert "lazy_ingested" not in result
    assert kegg.ingested == []
    assert builder.received == []


def test_lazy_ingest_result_includes_field_even_when_empty():
    """knowledge_query 返回体始终含 lazy_ingested 字段"""
    wm, _ = _make_lazy_manager(context_len=999, registry=None, builder=FakeBuilder())
    result = wm.execute_workflow("TP53 是什么")
    assert "lazy_ingested" in result
    assert result["lazy_ingested"] == []


def test_lazy_ingest_context_mutation_records_list():
    """补库成功时写入 context['lazy_ingested'] 供 UI 展示"""
    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    wm, _ = _make_lazy_manager(context_len=0, registry=registry, builder=FakeBuilder())
    context: dict = {}

    wm.execute_workflow("map04115 是什么", context=context)

    assert context.get("lazy_ingested")
    assert context["lazy_ingested"][0]["source"] == "kegg"


class KBMockClient(MockKnowledgeClientWithContext):
    """带 working_dir 的 mock：可指向临时 kv_store_full_entities/docs"""

    def __init__(self, working_dir, context_len=99999):
        super().__init__(context_len)
        self.working_dir = working_dir


def _write_kb_stubs(tmp_path, entity_names=None, doc_headers=None):
    """写入最小 kv_store_full_entities / full_docs 供 miss-check 读取。

    doc_headers: 专属文档头列表，如 ["# KEGG 通路: map04115", "# UniProt 蛋白: P04637"]
    """
    import json

    entities = {
        "doc-1": {"entity_names": entity_names or []},
    }
    contents = []
    for h in doc_headers or []:
        contents.append(f"{h}\nENTRY ...\nNAME stub\n")
    docs = {
        "doc-1": {"content": "\n".join(contents) if contents else ""},
    }
    (tmp_path / "kv_store_full_entities.json").write_text(
        json.dumps(entities, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "kv_store_full_docs.json").write_text(
        json.dumps(docs, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_lazy_ingest_skips_when_kb_already_has_id(tmp_path):
    """库里已有 map04115 专属文档 → 即使 context 很短也不重复入库"""
    _write_kb_stubs(
        tmp_path,
        entity_names=["map04115", "TP53"],
        doc_headers=["# KEGG 通路: map04115"],
    )
    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    client = KBMockClient(tmp_path, context_len=10)
    wm, _ = _make_lazy_manager(
        context_len=10, registry=registry, builder=FakeBuilder(), knowledge_client=client
    )

    result = wm.execute_workflow("map04115 是什么")

    assert result["lazy_ingested"] == []
    assert kegg.ingested == []


def test_lazy_ingest_triggers_when_kb_missing_id_despite_long_context(tmp_path):
    """map04110 只是实体交叉引用、无专属文档 → context 很长也触发补库"""
    _write_kb_stubs(
        tmp_path,
        # 实体表里有 map04110（其它通路引用），但没有专属文档头
        entity_names=["map04110", "map04115", "TP53"],
        doc_headers=["# KEGG 通路: map04115"],
    )
    kegg = FakeBioFetcher("kegg")
    registry = FakeBioRegistry({"kegg": kegg})
    client = KBMockClient(tmp_path, context_len=50000)  # 模拟 hybrid 噪声 context
    wm, _ = _make_lazy_manager(
        context_len=50000,
        registry=registry,
        builder=FakeBuilder(),
        knowledge_client=client,
    )

    result = wm.execute_workflow("map04110 是什么")

    assert result["lazy_ingested"] == [
        {"source": "kegg", "asset_id": "map04110", "title": "map04110"}
    ]
    assert kegg.ingested == ["map04110"]


def test_lazy_ingest_triggers_when_kb_missing_uniprot_accession(tmp_path):
    """库里没有 P04637 专属文档 → 触发 uniprot 补库；已有则跳过"""
    _write_kb_stubs(
        tmp_path,
        entity_names=["Q9Y2B4"],
        doc_headers=["# UniProt 蛋白: Q9Y2B4"],
    )
    uni = FakeBioFetcher("uniprot")
    registry = FakeBioRegistry({"uniprot": uni})
    client = KBMockClient(tmp_path, context_len=50000)
    wm, _ = _make_lazy_manager(
        context_len=50000, registry=registry, builder=FakeBuilder(), knowledge_client=client
    )

    result = wm.execute_workflow("P04637 的功能是什么")

    assert result["lazy_ingested"] == [
        {"source": "uniprot", "asset_id": "P04637", "title": "P04637"}
    ]

    # 已有 accession 专属文档 → 不补
    _write_kb_stubs(
        tmp_path,
        entity_names=["P04637"],
        doc_headers=["# UniProt 蛋白: P04637"],
    )
    uni2 = FakeBioFetcher("uniprot")
    registry2 = FakeBioRegistry({"uniprot": uni2})
    client2 = KBMockClient(tmp_path, context_len=50000)
    wm2, _ = _make_lazy_manager(
        context_len=50000,
        registry=registry2,
        builder=FakeBuilder(),
        knowledge_client=client2,
    )
    result2 = wm2.execute_workflow("P04637 的功能是什么")
    assert result2["lazy_ingested"] == []
    assert uni2.ingested == []


def test_lazy_ingest_kb_hit_genes_skip_even_with_short_context(tmp_path):
    """genes 已在实体表 → 不因 context 短而 search 兜底"""
    _write_kb_stubs(
        tmp_path,
        entity_names=["TP53"],
        doc_headers=["# KEGG 通路: map04115"],
    )
    kegg = FakeBioFetcher("kegg", metas=[_meta("kegg", "map04115")])
    uni = FakeBioFetcher("uniprot", metas=[_meta("uniprot", "P04637")])
    registry = FakeBioRegistry({"kegg": kegg, "uniprot": uni})
    client = KBMockClient(tmp_path, context_len=10)
    wm, _ = _make_lazy_manager(
        context_len=10, registry=registry, builder=FakeBuilder(), knowledge_client=client
    )

    result = wm.execute_workflow("TP53 是什么")

    assert result["lazy_ingested"] == []
    assert kegg.ingested == []
    assert uni.ingested == []


def test_lazy_ingest_kb_miss_gene_triggers_search(tmp_path):
    """genes 不在实体表 → 即使 context 长也走 search 兜底"""
    _write_kb_stubs(
        tmp_path,
        entity_names=["INS"],
        doc_headers=["# KEGG 通路: map04115"],
    )
    kegg = FakeBioFetcher("kegg", metas=[_meta("kegg", "map04115")])
    uni = FakeBioFetcher("uniprot", metas=[_meta("uniprot", "P04637")])
    registry = FakeBioRegistry({"kegg": kegg, "uniprot": uni})
    client = KBMockClient(tmp_path, context_len=50000)
    wm, _ = _make_lazy_manager(
        context_len=50000, registry=registry, builder=FakeBuilder(), knowledge_client=client
    )

    result = wm.execute_workflow("TP53 在癌症中的作用是什么")

    ids = {(item["source"], item["asset_id"]) for item in result["lazy_ingested"]}
    assert ("kegg", "map04115") in ids or ("uniprot", "P04637") in ids


def test_intent_parse_receives_context_history():
    from src.control.workflow_manager import WorkflowManager

    class SpyIntent:
        def __init__(self):
            self.seen_context = None

        def parse(self, user_input, context=None):
            self.seen_context = context
            return {"type": "general", "original_input": user_input}

        def extract_parameters(self, user_input):
            return {}

    spy = SpyIntent()
    wm = WorkflowManager(
        intent_parser=spy, knowledge_client=None, r_executor=None, visualizer=None
    )
    ctx = {"history": [{"role": "user", "content": "上一轮"}]}
    wm.execute_workflow("继续", context=ctx)
    assert spy.seen_context is ctx


def test_de_explanation_receives_real_stats(tmp_path):
    from src.control.workflow_manager import WorkflowManager

    csv_file = tmp_path / "in.csv"
    csv_file.write_text("gene,s1,s2\nG1,1,2\nG2,3,4\nG3,5,6\n", encoding="utf-8")

    class FakeIntent:
        def parse(self, user_input, context=None):
            return {"type": "analysis", "analysis_type": "differential_expression",
                    "original_input": user_input}

        def extract_parameters(self, user_input):
            return {"input_files": ["x.csv"]}

    class CapturingExplainer:
        def __init__(self):
            self.data = None

        def generate_llm_explanation(self, data, question):
            self.data = data
            return "ok"

    class FakeGen:
        def generate_code(self, analysis_type, params, method_context=None):
            return "# s"

    class FakeExec:
        def execute_code(self, code):
            out = str(csv_file.with_suffix(".de_results.csv"))
            with open(out, "w", encoding="utf-8") as f:
                f.write("gene,padj\nG1,0.001\nG2,0.2\nG3,0.8\n")

            class R:
                returncode = 0

            return R()

    exp = CapturingExplainer()
    wm = WorkflowManager(
        intent_parser=FakeIntent(), knowledge_client=None,
        r_executor=FakeExec(), visualizer=None,
        r_script_generator=FakeGen(), explainer=exp,
        require_script_confirmation=False,
    )
    context = {"downloaded_assets": [{"access_path": str(csv_file)}]}
    result = wm.execute_workflow("做差异表达", context=context)
    assert result["status"] == "success"
    assert exp.data["total_genes"] == 3
    assert exp.data["significant_genes"] == 1
    assert exp.data.get("input_file")
