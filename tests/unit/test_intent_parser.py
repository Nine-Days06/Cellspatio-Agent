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