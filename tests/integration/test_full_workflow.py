import tempfile
from pathlib import Path

import pytest

from src.main import CellSpatioAgent


def test_full_analysis_workflow(monkeypatch):
    """完整分析链：无 LLM → 旧路径 keyword 分析 + context 提供真实输入文件。"""
    monkeypatch.setattr("src.main.get_current_llm", lambda: (None, "test-model"))
    monkeypatch.setattr("src.main.LightRAGClient.__init__", lambda self, *a, **k: None)

    with tempfile.TemporaryDirectory() as temp_dir:
        csv_file = Path(temp_dir) / "counts.csv"
        csv_file.write_text("gene,sample1,sample2\nTP53,10,12\nGAPDH,100,110\n", encoding="utf-8")
        config = {
            "knowledge_dir": temp_dir,
            "llm": {"provider": "mock"},
        }
        agent = CellSpatioAgent(config)
        context = {
            "downloaded_assets": [
                {"source": "geo", "asset_id": "GSE_TEST", "access_path": str(csv_file)},
            ]
        }
        result = agent.execute_workflow("分析差异表达基因", context=context)

        # require_script_confirmation 默认 True → 首轮停在确认门（真实 HITL）
        assert result["status"] in ("needs_script_confirmation", "success")
        if result["status"] == "success":
            assert result["analysis_type"] == "differential_expression"
        else:
            assert result["analysis_type"] == "differential_expression"


def test_knowledge_query_workflow():
    """测试知识查询工作流（需要配置 embedding_func）"""
    # 此测试需要配置 embedding 函数才能运行
    # 跳过测试，因为需要外部 API 配置
    pytest.skip("需要配置 embedding_func 才能运行此测试")


def test_ui_rendering():
    """测试 UI 渲染（模拟）"""
    # 这个测试需要 Streamlit 测试框架
