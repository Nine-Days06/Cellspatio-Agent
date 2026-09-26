"""KGMemory：工作流记录增量写入 LightRAG 测试。"""
import tempfile
from pathlib import Path
import pytest
import numpy as np
from src.control.kg_memory import KGMemory
from src.schemas.workflow import WorkflowRun, WorkflowIntent, WorkflowInput, WorkflowStep, WorkflowOutput, TerminalStatus, StepType, AnalysisType, IntentType, StepOutput
from lightrag.utils import EmbeddingFunc


def _make_run() -> "WorkflowRun":
    return WorkflowRun(
        run_id="test-run-kgmem-001",
        intent=WorkflowIntent(type=IntentType.ANALYSIS, analysis_type=AnalysisType.DIFFERENTIAL_EXPRESSION, original_input="test"),
        params={"input_file": "data.csv"},
        input=WorkflowInput(user_input="test"),
        steps=[
            WorkflowStep(
                step_id="s1",
                step_type=StepType.ANALYSIS,
                tool="run_analysis",
                params={"analysis_type": "differential_expression"},
                output=StepOutput(status=TerminalStatus.SUCCESS, result={"result_file": "out.de_results.csv"}),
            ),
        ],
        outputs=[
            WorkflowOutput(name="de_results", path="out.de_results.csv", type="csv", meta={"genes": 100}),
        ],
    )


def test_kg_memory_ingest_and_query():
    """测试 ingest 和 query 在同一个 KGMemory 实例上工作。"""
    with tempfile.TemporaryDirectory() as tmp:
        kg_mem = KGMemory(working_dir=Path(tmp) / "kg")
        run = _make_run()
        kg_mem.ingest(run)
        # 验证 LightRAG 写入（文件已创建在 workspace 子目录中）
        kg_dir = Path(tmp) / "kg"
        # 找到 workspace 子目录
        workspace_dirs = list(kg_dir.glob("kg_*"))
        assert len(workspace_dirs) == 1
        workspace_dir = workspace_dirs[0]
        assert (workspace_dir / "vdb_entities.json").exists()
        # 查询实体 - mock LLM 可能不返回实体，但不应报错
        entities = kg_mem.query_entities("test-run-kgmem-001")
        assert isinstance(entities, list)


def test_kg_memory_accepts_custom_funcs(tmp_path):
    """注入的 llm/embedding 函数必须被采纳（而非硬编码 mock）。"""
    calls = {"llm": 0, "embed": 0}

    async def fake_llm(prompt: str, **kwargs) -> str:
        calls["llm"] += 1
        return '{"entities": [], "relationships": []}'

    async def fake_embed(texts, **kwargs):
        calls["embed"] += 1
        return np.array([[0.2] * 768 for _ in texts], dtype=np.float32)

    mem = KGMemory(
        working_dir=tmp_path / "kg_custom",
        llm_model_func=fake_llm,
        embedding_func=EmbeddingFunc(embedding_dim=768, max_token_size=8192, func=fake_embed),
    )
    mem.ingest(_make_run())  # 沿用该文件已有的 fake execution 构造方式
    assert calls["llm"] >= 1  # ainsert 触发实体提取 → 走注入的 llm
    assert calls["embed"] >= 1  # embedding 也被调用
    mem.close()