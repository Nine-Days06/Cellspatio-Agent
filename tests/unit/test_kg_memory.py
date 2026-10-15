"""KGMemory：工作流记录增量写入 LightRAG 测试。"""
import tempfile
import uuid
from pathlib import Path

import numpy as np
from lightrag.utils import EmbeddingFunc

from src.control.kg_memory import KGMemory
from src.schemas.workflow import (
    AnalysisType,
    IntentType,
    StepOutput,
    StepType,
    TerminalStatus,
    WorkflowInput,
    WorkflowIntent,
    WorkflowOutput,
    WorkflowRun,
    WorkflowStep,
)


def _unique_ws(prefix: str) -> str:
    """PG 持久化下，测试须用唯一 workspace 隔离，否则固定 run_id 被跨运行去重跳过。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


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
    """测试 ingest 和 query 在同一个 KGMemory 实例上工作（PG 后端）。"""
    with tempfile.TemporaryDirectory() as tmp:
        kg_dir = Path(tmp) / "kg"
        kg_mem = KGMemory(working_dir=kg_dir, workspace=_unique_ws("test_ingest"))
        try:
            run = _make_run()
            kg_mem.ingest(run)
            # 查询实体 - mock LLM 可能不返回实体，但不应报错
            entities = kg_mem.query_entities("test-run-kgmem-001")
            assert isinstance(entities, list)
        finally:
            kg_mem.close()


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
        workspace=_unique_ws("test_custom"),
        llm_model_func=fake_llm,
        embedding_func=EmbeddingFunc(embedding_dim=768, max_token_size=8192, func=fake_embed),
    )
    try:
        mem.ingest(_make_run())  # 沿用该文件已有的 fake execution 构造方式
        assert calls["llm"] >= 1  # ainsert 触发实体提取 → 走注入的 llm
        assert calls["embed"] >= 1  # embedding 也被调用
    finally:
        mem.close()


def test_kg_memory_registers_atexit_close(monkeypatch, tmp_path):
    """构造时必须向 atexit 注册 close（进程退出时优雅关闭事件循环）。

    close 经 register_atexit 包装以抑制退出期 logging 噪声，故按包装器的
    atexit_target 属性回溯到原回调。
    """
    import atexit
    registered = []
    monkeypatch.setattr(atexit, "register", lambda cb: registered.append(cb))
    mem = KGMemory(working_dir=tmp_path / "kg_atexit")
    targets = [getattr(cb, "atexit_target", cb) for cb in registered]
    assert any(
        getattr(cb, "__func__", None) is KGMemory.close
        and getattr(cb, "__self__", None) is mem
        for cb in targets
    )
    mem.close()