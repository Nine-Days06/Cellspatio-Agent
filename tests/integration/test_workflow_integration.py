"""工作流集成测试：验证"意图解析 → 工作流管理 → R 脚本生成"端到端链路。

不依赖 R 环境、不调用外部 API，使用 Mock 类替代外部组件。
"""

import tempfile
from pathlib import Path

from src.control.intent_parser import IntentParser
from src.control.r_script_generator import RScriptGenerator
from src.control.workflow_manager import WorkflowManager


class MockKnowledgeClient:
    """模拟知识库客户端"""

    def __init__(self):
        self.query_count = 0

    def query(self, query: str) -> str:
        self.query_count += 1
        return f"【知识库】{query}：TP53 是一种重要的抑癌基因，调控细胞周期与凋亡。"


class MockRExecutor:
    """模拟 R 执行器"""


class MockVisualizer:
    """模拟可视化器"""


def _make_workflow_manager() -> WorkflowManager:
    """构造带真实 IntentParser 与 Mock 外部组件的 WorkflowManager"""
    return WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=MockKnowledgeClient(),
        r_executor=MockRExecutor(),
        visualizer=MockVisualizer(),
    )


def test_analysis_workflow_full_chain():
    """测试分析工作流完整链路：意图解析 → 工作流管理"""
    manager = _make_workflow_manager()

    result = manager.execute_workflow("分析差异表达基因")

    assert result['status'] == 'success'
    assert result['analysis_type'] == 'differential_expression'


def test_visualization_intent_and_script_generation():
    """测试可视化意图解析与 R 脚本生成的整合"""
    intent = IntentParser().parse("画一个火山图")
    assert intent['type'] == 'analysis'
    assert intent['analysis_type'] == 'visualization'

    code = RScriptGenerator().generate_code(intent['analysis_type'], {'plot_type': 'volcano'})

    assert '#!/usr/bin/env Rscript' in code
    assert 'volcano' in code
    assert 'Volcano Plot' in code


def test_r_script_generation_to_file():
    """测试 R 脚本生成并落盘，验证生成器 → 文件管线（无需 R）"""
    generator = RScriptGenerator()
    code = generator.generate_code(
        'differential_expression',
        {'input_file': 'data.csv', 'output_file': 'results.csv'},
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = Path(temp_dir) / 'de_analysis.R'
        script_path.write_text(code, encoding='utf-8')

        assert script_path.exists()
        content = script_path.read_text(encoding='utf-8')
        assert content.startswith('#!/usr/bin/env Rscript')
        assert 'read.csv' in content


def test_knowledge_query_workflow():
    """测试知识查询工作流：真实意图解析 + Mock 知识库"""
    knowledge_client = MockKnowledgeClient()
    manager = WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=knowledge_client,
        r_executor=MockRExecutor(),
        visualizer=MockVisualizer(),
    )

    result = manager.execute_workflow("TP53 是什么基因？")

    assert result['status'] == 'success'
    assert result['type'] == 'knowledge_response'
    assert 'TP53' in result['response']
    assert knowledge_client.query_count == 1