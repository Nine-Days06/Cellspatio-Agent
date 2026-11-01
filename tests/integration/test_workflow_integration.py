"""工作流集成测试：验证"意图解析 → 工作流管理 → R 脚本生成"端到端链路。

不依赖 R 环境、不调用外部 API，使用 Mock 类替代外部组件。
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.control.agent_runtime import AgentRuntime
from src.control.intent_parser import IntentParser
from src.control.r_script_generator import RScriptGenerator
from src.control.workflow_manager import WorkflowManager
from tests.unit.fakes import FakeExec


class MockKnowledgeClient:
    """模拟知识库客户端"""

    def __init__(self):
        self.query_count = 0

    def query(self, query: str) -> str:
        self.query_count += 1
        return f"【知识库】{query}：TP53 是一种重要的抑癌基因，调控细胞周期与凋亡。"


class MockRExecutor:
    """模拟 R 执行器"""

    def execute_code(self, code):
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="", stderr="")


class MockVisualizer:
    """模拟可视化器"""


def _make_workflow_manager() -> WorkflowManager:
    """构造带真实 IntentParser 与 Mock 外部组件的 WorkflowManager"""
    return WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=MockKnowledgeClient(),
        r_executor=MockRExecutor(),
        visualizer=MockVisualizer(),
        r_script_generator=RScriptGenerator(),
    )


def test_analysis_workflow_full_chain():
    """测试分析工作流完整链路：意图解析 → 工作流管理（无数据时返回 needs_input）"""
    manager = _make_workflow_manager()

    result = manager.execute_workflow("分析差异表达基因")

    # 无数据时 → needs_input（新语义：不再伪造成功）
    assert result["status"] == "needs_input"
    assert result["analysis_type"] == "differential_expression"
    assert (
        "input_files" in result["message"]
        or "下载" in result["message"]
        or "数据" in result["message"]
    )


@pytest.mark.parametrize(
    ("counts", "expected_status"),
    [
        (None, "needs_input"),
        ("0.5", "error"),
        ("2", "needs_script_confirmation"),
        ("10x", "needs_script_confirmation"),
        ("10x-invalid", "error"),
    ],
)
def test_single_cell_input_gate_through_agent_runtime(tmp_path, counts, expected_status):
    """真实工具分发与工作流共用门禁，非法输入不生成或执行脚本。"""
    generator = Mock(wraps=RScriptGenerator())
    executor = FakeExec(track_codes=True)
    manager = WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=None,
        r_executor=executor,
        visualizer=None,
        r_script_generator=generator,
        require_script_confirmation=True,
    )
    llm = Mock()
    tool_call = SimpleNamespace(
        id="call_single_cell",
        function=SimpleNamespace(
            name="run_analysis", arguments=json.dumps({"analysis_type": "single_cell"})
        ),
    )
    llm.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tool_call]))]
    )
    runtime = AgentRuntime(llm_client=llm, model="fake-model", workflow_manager=manager)
    context = {}
    if counts is not None:
        if counts.startswith("10x"):
            input_file = tmp_path / "sample.v1"
            input_file.mkdir()
            value = "1" if counts == "10x" else "0.5"
            (input_file / "matrix.mtx").write_text(
                f"%%MatrixMarket matrix coordinate integer general\n1 2 1\n1 1 {value}\n",
                encoding="utf-8",
            )
            (input_file / "barcodes.tsv").write_text("c1\nc2\n", encoding="utf-8")
            (input_file / "features.tsv").write_text("id1\tG1\tGene Expression\n", encoding="utf-8")
        else:
            input_file = tmp_path / "counts.csv"
            input_file.write_text(f"gene,c1,c2\nG1,1,{counts}\n", encoding="utf-8")
        context["downloaded_assets"] = [{"access_path": str(input_file)}]

    result = runtime.execute("对这份数据做单细胞聚类", context)

    assert result["status"] == expected_status
    assert result["analysis_type"] == "single_cell"
    llm.chat.completions.create.assert_called_once()
    assert executor.codes == []
    assert "script_approved" not in context
    if expected_status == "needs_script_confirmation":
        generator.generate_code.assert_called_once()
        assert "CreateSeuratObject" in result["script"]
        assert "round(mat)" not in result["script"]
        if counts == "10x":
            assert "Read10X" in result["script"] and "ReadMtx" in result["script"]
            assert result["params"]["output_file"].endswith("sample.v1.sc_clusters.csv")
    else:
        generator.generate_code.assert_not_called()
        assert "script" not in result


def test_visualization_intent_and_script_generation():
    """测试可视化意图解析与 R 脚本生成的整合"""
    intent = IntentParser().parse("画一个火山图")
    assert intent["type"] == "analysis"
    assert intent["analysis_type"] == "visualization"

    code = RScriptGenerator().generate_code(intent["analysis_type"], {"plot_type": "volcano"})

    assert "#!/usr/bin/env Rscript" in code
    assert "volcano" in code
    assert "Volcano Plot" in code


def test_r_script_generation_to_file():
    """测试 R 脚本生成并落盘，验证生成器 → 文件管线（无需 R）"""
    generator = RScriptGenerator()
    code = generator.generate_code(
        "differential_expression",
        {"input_file": "data.csv", "output_file": "results.csv"},
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = Path(temp_dir) / "de_analysis.R"
        script_path.write_text(code, encoding="utf-8")

        assert script_path.exists()
        content = script_path.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env Rscript")
        assert "read.csv" in content


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

    assert result["status"] == "success"
    assert result["type"] == "knowledge_response"
    assert "TP53" in result["response"]
    assert knowledge_client.query_count == 1
