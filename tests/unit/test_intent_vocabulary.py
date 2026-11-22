"""意图词表契约：解析器、分类器、工具 schema、R 模板必须共用同一份分析类型。"""


def test_supported_types_match_workflow_schema_and_r_templates():
    from src.control.intent_parser import IntentParser
    from src.control.intent_vocabulary import ANALYSIS_KEYWORDS, ANALYSIS_TYPES
    from src.control.r_script_generator import ANALYSIS_TYPE_DESC, RScriptGenerator
    from src.control.tools import SUPPORTED_ANALYSIS_TYPES
    from src.schemas.workflow import AnalysisType

    expected = ["differential_expression", "single_cell", "spatial"]
    assert list(ANALYSIS_TYPES) == expected
    assert list(ANALYSIS_KEYWORDS) == expected
    assert list(SUPPORTED_ANALYSIS_TYPES) == expected
    assert list(ANALYSIS_TYPE_DESC) == expected
    assert [t.value for t in AnalysisType] == expected
    assert list(IntentParser.INTENT_TYPES["analysis"]["sub_types"]) == expected

    generator = RScriptGenerator()
    for analysis_type in expected:
        assert not generator.generate_code(analysis_type, {}).startswith("# 不支持的分析类型")


def test_classifier_and_parser_share_keywords():
    from src.control.classifier import TaskClassifier
    from src.control.intent_vocabulary import ANALYSIS_KEYWORDS, KNOWLEDGE_KEYWORDS

    assert TaskClassifier.MODALITY_KEYWORDS["knowledge"]["query_knowledge"] == KNOWLEDGE_KEYWORDS
    for analysis_type, keywords in ANALYSIS_KEYWORDS.items():
        classified = TaskClassifier.MODALITY_KEYWORDS["analysis"][analysis_type]
        assert set(keywords) <= set(classified)
