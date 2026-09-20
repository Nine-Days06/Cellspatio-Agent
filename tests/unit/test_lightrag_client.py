import tempfile


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
