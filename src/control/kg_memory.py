"""KGMemory：WorkflowRecorder → LightRAG 增量写入。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
from lightrag import LightRAG
from lightrag.lightrag import QueryParam
from lightrag.utils import EmbeddingFunc


async def _mock_llm(prompt: str, **kwargs) -> str:
    """模拟 LLM 函数：返回 LightRAG 期望的 JSON 格式（含代码围栏）。"""
    # 关键词提取期望返回 JSON 对象
    if "keyword" in prompt.lower() or "keywords" in prompt.lower():
        return '```json\n{"high_level_keywords": ["test", "run"], "low_level_keywords": ["test-run-001", "analysis"]}\n```'
    # 实体/关系提取期望返回特定格式 - 需要包含代码围栏
    if "entity" in prompt.lower() or "relation" in prompt.lower() or "extract" in prompt.lower():
        return '```json\n{"entities": [{"entity_name": "test-run-001", "entity_type": "workflow", "description": "Test workflow run", "source_id": "test-run-001"}], "relationships": []}\n```'
    # 所有其他情况（查询、摘要、生成等）返回有意义的文本
    return "test-run-001: 差异表达分析测试运行，包含步骤和输出结果。单细胞聚类最佳参数：resolution=0.5, n_neighbors=15。TP53 是重要的肿瘤抑制基因。"


async def _mock_embedding(texts: list[str], **kwargs) -> np.ndarray:
    """模拟嵌入函数：返回固定维度的向量。"""
    return np.array([[0.1] * 768 for _ in texts], dtype=np.float32)


class KGMemory:
    """WorkflowRecorder → LightRAG 知识图谱增量写入。"""

    def __init__(self, working_dir: Path | str, workspace: str | None = None):
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)
        
        embedding_func = EmbeddingFunc(
            embedding_dim=768,
            func=_mock_embedding,
            max_token_size=8192,
        )
        
        # Use workspace for LightRAG's shared storage isolation
        # If not provided, use working_dir name + random suffix for test isolation
        if workspace is None:
            import uuid
            workspace = f"{self.working_dir.name}_{uuid.uuid4().hex[:8]}"
        
        self._rag = LightRAG(
            working_dir=str(self.working_dir),
            embedding_func=embedding_func,
            llm_model_func=_mock_llm,
            workspace=workspace,
        )
        self._workspace = workspace
        # 创建专用事件循环，在其中初始化存储并执行所有操作
        # 避免与 pytest-asyncio 的全局事件循环冲突
        self._loop = asyncio.new_event_loop()
        self._initialized = False
        # 在专用循环中初始化存储
        self._loop.run_until_complete(self._rag.initialize_storages())
        self._initialized = True

    def _get_loop(self):
        """获取专用事件循环。"""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def _ensure_initialized(self) -> None:
        """确保存储已初始化。"""
        if not self._initialized:
            self._loop.run_until_complete(self._rag.initialize_storages())
            self._initialized = True

    def ingest(self, execution) -> None:
        """将 WorkflowExecution 或 WorkflowRun 增量写入 LightRAG。"""
        self._ensure_initialized()
        loop = self._get_loop()
        
        # 1. 写入运行记录实体
        run_text = f"Run {execution.run_id}: {execution.intent.original_input}"
        loop.run_until_complete(self._rag.ainsert(run_text, ids=[f"run:{execution.run_id}"]))

        # 2. 写入步骤实体
        for step in execution.steps:
            step_text = f"Step {step.step_id}: {step.tool} {step.params}"
            loop.run_until_complete(self._rag.ainsert(step_text, ids=[f"step:{execution.run_id}:{step.step_id}"]))

        # 3. 写入输出实体（兼容 WorkflowRun，WorkflowExecution 无 outputs）
        outputs = getattr(execution, "outputs", None)
        if outputs:
            for out in outputs:
                out_text = f"Output {out.name}: {out.path}"
                loop.run_until_complete(self._rag.ainsert(out_text, ids=[f"output:{execution.run_id}:{out.name}"]))

    def query_entities(self, query: str) -> list[str]:
        """实体检索：返回相关实体文本片段。"""
        self._ensure_initialized()
        loop = self._get_loop()
        param = QueryParam(mode="hybrid")
        result = loop.run_until_complete(self._rag.aquery(query, param=param))
        return [str(r) for r in result] if result else []

    def query_relations(self, entity: str) -> list[dict]:
        """关系查询：返回实体关系。"""
        self._ensure_initialized()
        loop = self._get_loop()
        param = QueryParam(mode="hybrid")
        result = loop.run_until_complete(self._rag.aquery(f"Relations of {entity}", param=param))
        return [{"text": str(r)} for r in result] if result else []