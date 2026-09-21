import tempfile


class FakeRAG:
    def __init__(self):
        self.inserted = []
    def insert(self, input_data):
        if isinstance(input_data, list):
            self.inserted.extend(input_data)
        else:
            self.inserted.append(input_data)


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