def test_parse_analysis_intent():
    """Test parsing analysis intent from natural language"""
    from src.control.intent_parser import IntentParser
    
    parser = IntentParser()
    
    # 测试差异表达分析意图
    intent = parser.parse("我想分析 RNA-seq 数据的差异表达基因")
    assert intent['type'] == 'analysis'
    assert intent['analysis_type'] == 'differential_expression'
    
    # 测试知识查询意图
    intent = parser.parse("TP53 在癌症中的作用是什么？")
    assert intent['type'] == 'knowledge_query'


def test_parse_fetch_data_intent():
    """识别数据集下载意图并提取来源与编号"""
    from src.control.intent_parser import IntentParser
    
    parser = IntentParser()
    
    intent = parser.parse("帮我下载 GSE123456 数据集")
    assert intent['type'] == 'fetch_data'
    
    params = parser.extract_parameters("帮我下载 GSE123456 数据集")
    assert 'geo' in params['sources']
    assert 'GSE123456' in params['dataset_ids']


def test_parse_uniprot_query_intent():
    from src.control.intent_parser import IntentParser
    
    parser = IntentParser()
    intent = parser.parse("下载 TP53 蛋白信息")
    assert intent['type'] == 'fetch_data'
    assert 'uniprot' in parser.extract_parameters("下载 TP53 蛋白信息")['sources']


def test_parse_analysis_priority_over_fetch():
    """含分析关键词时 analysis 优先（如『下载后做差异分析』）"""
    from src.control.intent_parser import IntentParser
    
    parser = IntentParser()
    intent = parser.parse("下载数据集并做差异表达分析")
    assert intent['type'] == 'analysis'
    assert intent['analysis_type'] == 'differential_expression'


def test_parse_single_cell_intent():
    from src.control.intent_parser import IntentParser

    parser = IntentParser()
    intent = parser.parse("对这份 scRNA-seq 数据做单细胞聚类和 UMAP")
    assert intent["type"] == "analysis"
    assert intent["analysis_type"] == "single_cell"


def test_parse_spatial_intent():
    from src.control.intent_parser import IntentParser

    parser = IntentParser()
    intent = parser.parse("分析这个 Visium 空间转录组数据")
    assert intent["type"] == "analysis"
    assert intent["analysis_type"] == "spatial"


def test_subtypes_listed_in_system_prompt():
    from src.control.intent_parser import IntentParser

    parser = IntentParser()
    assert "single_cell" in parser.system_prompt
    assert "spatial" in parser.system_prompt


def test_parse_falls_back_when_llm_raises_provider_error():
    """LLM 提供商异常（如占位 key 触发 401 AuthenticationError）应降级关键词解析而非抛出"""
    from src.control.intent_parser import IntentParser

    class FakeProviderError(Exception):
        """模拟 SDK 认证异常（openai.AuthenticationError 等，非 ValueError 子类）"""

    class FakeCompletions:
        def create(self, **kwargs):
            raise FakeProviderError("401 Unauthorized: invalid api key")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    parser = IntentParser(llm_client=FakeClient())
    intent = parser.parse("我想分析 RNA-seq 数据的差异表达基因")
    assert intent["type"] == "analysis"
    assert intent["analysis_type"] == "differential_expression"