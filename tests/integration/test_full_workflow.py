import tempfile

import pytest

from src.main import CellSpatioAgent


def test_full_analysis_workflow():
    """测试完整分析工作流"""
    with tempfile.TemporaryDirectory() as temp_dir:
        # 配置测试环境
        config = {
            'knowledge_dir': temp_dir,
            'llm': {'provider': 'mock'},  # 使用 mock LLM
        }

        agent = CellSpatioAgent(config)
        
        # 测试分析工作流
        result = agent.execute_workflow("分析差异表达基因")
        assert result['status'] == 'success'
        assert 'analysis_type' in result


def test_knowledge_query_workflow():
    """测试知识查询工作流（需要配置 embedding_func）"""
    # 此测试需要配置 embedding 函数才能运行
    # 跳过测试，因为需要外部 API 配置
    pytest.skip("需要配置 embedding_func 才能运行此测试")


def test_ui_rendering():
    """测试 UI 渲染（模拟）"""
    # 这个测试需要 Streamlit 测试框架
