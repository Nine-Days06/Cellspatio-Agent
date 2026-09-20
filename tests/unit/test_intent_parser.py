
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