import tempfile


class FakeRAG:
    def __init__(self):
        self.inserted = []
        self.file_paths_seen = []
        self.query_llm_calls = []
        self.query_llm_payload = {
            "llm_response": {"content": "答案正文\n\n### References\n- [1] 旧引用"},
            "data": {"references": [
                {"reference_id": "1", "file_path": "https://www.kegg.jp/pathway/map04115"},
            ]},
        }

    def insert(self, input_data, file_paths=None, **kwargs):
        if file_paths is not None:
            self.file_paths_seen.append(file_paths)
        if isinstance(input_data, list):
            self.inserted.extend(input_data)
        else:
            self.inserted.append(input_data)

    def query_llm(self, question, param=None, **kwargs):
        self.query_llm_calls.append((question, param))
        return self.query_llm_payload


def _client_with_fake_rag(working_dir, config=None):
    from src.knowledge.lightrag_client import LightRAGClient

    client = LightRAGClient(working_dir=working_dir, config=config or {})
    client._rag = FakeRAG()
    return client


def test_lightrag_initialization():
    """Test LightRAG initialization"""
    from src.knowledge.lightrag_client import LightRAGClient
    
    with tempfile.TemporaryDirectory() as temp_dir:
        client = LightRAGClient(working_dir=temp_dir)
        assert client is not None
        assert str(client.working_dir) == temp_dir

def test_insert_document():
    """Test document insertion"""
    from src.knowledge.lightrag_client import LightRAGClient
    
    with tempfile.TemporaryDirectory() as temp_dir:
        client = LightRAGClient(working_dir=temp_dir)
        # 注意：实际测试需要 mock LLM 调用
        # 这里只是验证接口
        assert hasattr(client, 'insert_document')


def test_initialize_rag_uses_configured_models(tmp_path, monkeypatch):
    from src.knowledge.lightrag_client import LightRAGClient

    captured = {}

    class FakeRagForInit:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def initialize_storages(self):
            pass

    monkeypatch.setattr("lightrag.LightRAG", FakeRagForInit)
    monkeypatch.setattr(
        "src.knowledge.llm_factory.build_llm_func",
        lambda provider: (object(), "deepseek-v4-flash"),
    )
    monkeypatch.setattr(
        "src.knowledge.llm_factory.build_embedding_func",
        lambda custom=None: object(),
    )

    client = LightRAGClient(working_dir=tmp_path, config={"ollama_url": "http://127.0.0.1:11434"})
    client._initialize_rag()
    assert captured["llm_model_name"] == "deepseek-v4-flash"
    assert captured["embedding_func"] is not None


def test_insert_documents_batch(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    count = client.insert_documents(["doc-a", "doc-b"])
    assert count == 2
    assert client._rag.inserted == ["doc-a", "doc-b"]


def test_insert_documents_empty_returns_zero(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    assert client.insert_documents([]) == 0


def test_insert_document_single_still_works(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    client.insert_document("single-doc")
    assert client._rag.inserted == ["single-doc"]


def test_insert_documents_derives_kegg_file_path(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    client.insert_documents(["# KEGG 通路: map04115\n来源：https://www.kegg.jp/pathway/map04115\n"])
    assert client._rag.file_paths_seen == [
        ["https://www.kegg.jp/pathway/map04115"]
    ]


def test_strip_references_section_cut_en_and_zh():
    from src.knowledge.lightrag_client import strip_references_section

    text = "TP53 抑癌。\n\n### References\n- [1] foo\n- [2] bar\n"
    assert strip_references_section(text) == "TP53 抑癌。\n"
    zh = "答案。\n\n## 参考文献\n[1] 甲\n[2] 乙\n"
    assert strip_references_section(zh) == "答案。\n"
    assert strip_references_section("无引用段。") == "无引用段。"


def test_format_reference_kegg_link():
    from src.knowledge.lightrag_client import format_reference

    md = format_reference({
        "reference_id": "1",
        "file_path": "https://www.kegg.jp/pathway/map04115",
    })
    assert md == "[KEGG map04115](https://www.kegg.jp/pathway/map04115)"


def test_expand_file_path_basename_to_url():
    from src.knowledge.lightrag_client import expand_file_path

    # LightRAG 只存 basename
    assert expand_file_path("map04115") == "https://www.kegg.jp/pathway/map04115"
    assert expand_file_path("P04637") == "https://www.uniprot.org/uniprotkb/P04637"
    assert expand_file_path("Q9NQ88") == "https://www.uniprot.org/uniprotkb/Q9NQ88"
    assert expand_file_path("acc.cgi?acc=GSE123456") == (
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123456"
    )
    assert expand_file_path("https://example.com/x") == "https://example.com/x"
    assert expand_file_path("unknown_source") == "unknown_source"
    assert expand_file_path("") == ""


def test_format_reference_expands_basename():
    from src.knowledge.lightrag_client import format_reference

    md = format_reference({"reference_id": "1", "file_path": "map04110"})
    assert md == "[KEGG map04110](https://www.kegg.jp/pathway/map04110)"
    md2 = format_reference({"reference_id": "2", "file_path": "P04637"})
    assert md2 == "[UniProt P04637](https://www.uniprot.org/uniprotkb/P04637)"


def test_query_with_references_strips_and_returns_refs(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    # 模拟 LightRAG 只回 basename
    client._rag.query_llm_payload["data"]["references"] = [
        {"reference_id": "1", "file_path": "map04115"},
    ]
    result = client.query_with_references("map04115 是什么")
    assert "References" not in result["response"]
    assert result["response"].startswith("答案正文")
    assert result["references"][0]["file_path"] == (
        "https://www.kegg.jp/pathway/map04115"
    )
    # include_references 打开
    _q, param = client._rag.query_llm_calls[0]
    assert param.include_references is True

def test_resolve_max_async_prefers_env(monkeypatch):
    """MAX_ASYNC 环境变量应成为并发值（.env 可调，无需改代码）"""
    from src.knowledge.lightrag_client import resolve_max_async

    monkeypatch.setenv("MAX_ASYNC", "12")
    assert resolve_max_async() == 12


def test_resolve_max_async_falls_back_to_none(monkeypatch):
    """未设环境变量时返回 None（由实例化处回落默认 2）"""
    from src.knowledge.lightrag_client import resolve_max_async

    monkeypatch.delenv("MAX_ASYNC", raising=False)
    assert resolve_max_async() is None


def test_resolve_max_async_ignores_invalid_env(monkeypatch):
    """非法值（非数字/<=0）视为未设置，返回 None"""
    from src.knowledge.lightrag_client import resolve_max_async

    for bad in ("abc", "0", "-3", ""):
        monkeypatch.setenv("MAX_ASYNC", bad)
        assert resolve_max_async() is None, bad


def test_client_uses_resolved_max_async(monkeypatch, tmp_path):
    """客户端显式传参优先；未传时用 MAX_ASYNC 解析值"""
    from src.knowledge import lightrag_client as m

    wd = str(tmp_path / "kb")
    monkeypatch.setenv("MAX_ASYNC", "9")
    assert m.LightRAGClient(working_dir=wd)._max_async == 9
    assert m.LightRAGClient(working_dir=wd, max_async=3)._max_async == 3
    monkeypatch.delenv("MAX_ASYNC", raising=False)
    assert m.LightRAGClient(working_dir=wd)._max_async is None


def test_resolve_max_parallel_insert_prefers_env(monkeypatch):
    """MAX_PARALLEL_INSERT 环境变量驱动文档级并发"""
    from src.knowledge.lightrag_client import resolve_max_parallel_insert

    monkeypatch.setenv("MAX_PARALLEL_INSERT", "50")
    assert resolve_max_parallel_insert() == 50


def test_resolve_max_parallel_insert_ignores_invalid_env(monkeypatch):
    """非法值（非数字/<=0）视为未设置，返回 None"""
    from src.knowledge.lightrag_client import resolve_max_parallel_insert

    for bad in ("abc", "0", "-3", ""):
        monkeypatch.setenv("MAX_PARALLEL_INSERT", bad)
        assert resolve_max_parallel_insert() is None, bad
    monkeypatch.delenv("MAX_PARALLEL_INSERT", raising=False)
    assert resolve_max_parallel_insert() is None


def test_client_uses_resolved_max_parallel_insert(monkeypatch, tmp_path):
    """客户端显式传参优先；未传时用 MAX_PARALLEL_INSERT 解析值"""
    from src.knowledge import lightrag_client as m

    wd = str(tmp_path / "kb")
    monkeypatch.setenv("MAX_PARALLEL_INSERT", "50")
    assert m.LightRAGClient(working_dir=wd)._max_parallel_insert == 50
    assert m.LightRAGClient(working_dir=wd, max_parallel_insert=7)._max_parallel_insert == 7
    monkeypatch.delenv("MAX_PARALLEL_INSERT", raising=False)
    assert m.LightRAGClient(working_dir=wd)._max_parallel_insert is None


def test_build_storage_kwargs_pg_four_storages(monkeypatch):
    """四类 PostgreSQL storage + 补齐连接环境变量"""
    from src.knowledge import lightrag_client as m

    for k in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
              "POSTGRES_PASSWORD", "POSTGRES_DATABASE"):
        monkeypatch.delenv(k, raising=False)
    kw = m.build_storage_kwargs()
    assert kw == {
        "kv_storage": "PGKVStorage",
        "vector_storage": "PGVectorStorage",
        "graph_storage": "PGTableGraphStorage",
        "doc_status_storage": "PGDocStatusStorage",
    }
    import os
    # check_storage_env_vars 要求这三个必须存在于 os.environ
    assert os.environ["POSTGRES_USER"] == "postgres"
    assert os.environ["POSTGRES_DATABASE"] == "rag"
    assert "POSTGRES_PASSWORD" in os.environ
    assert os.environ["POSTGRES_PORT"] == "5433"


def test_build_storage_kwargs_respects_existing_env(monkeypatch):
    """已有 POSTGRES_* 环境变量不被覆盖（setdefault 语义）"""
    from src.knowledge import lightrag_client as m

    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_DATABASE", "mydb")
    m.build_storage_kwargs()
    import os
    assert os.environ["POSTGRES_PORT"] == "6543"
    assert os.environ["POSTGRES_DATABASE"] == "mydb"
