"""KGMemory：WorkflowRecorder → LightRAG 增量写入。"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from lightrag import LightRAG
from lightrag.lightrag import QueryParam
from lightrag.utils import EmbeddingFunc

from src.knowledge.async_runtime import register_atexit
from src.knowledge.async_runtime import runtime as async_runtime
from src.knowledge.lightrag_client import build_storage_kwargs, default_workspace_for

logger = logging.getLogger(__name__)


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

    def __init__(self, working_dir: Path | str, workspace: str | None = None,
                 llm_model_func=None, embedding_func=None):
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)

        # 生产传入 llm_factory 的真实函数；缺省保持 mock（测试/评测态）
        if embedding_func is None:
            embedding_func = EmbeddingFunc(
                embedding_dim=768,
                func=_mock_embedding,
                max_token_size=8192,
            )
        if llm_model_func is None:
            llm_model_func = _mock_llm

        # PG 存储按 workspace 隔离；默认取目录名（稳定），不设随机后缀——
        # 随机 workspace 会让每次启动都读不到历史记忆
        self._workspace = (
            workspace if workspace is not None else default_workspace_for(self.working_dir)
        )

        self._rag = LightRAG(
            working_dir=str(self.working_dir),
            embedding_func=embedding_func,
            llm_model_func=llm_model_func,
            workspace=self._workspace,
            **build_storage_kwargs(),
        )
        register_atexit(self.close)
        self._initialized = False
        async_runtime.run(self._rag.initialize_storages())
        self._initialized = True

    def close(self) -> None:
        """finalize 存储（释放 PG 池引用）；幂等。"""
        if self._rag is None or not hasattr(self._rag, "finalize_storages"):
            return
        try:
            async_runtime.run(self._rag.finalize_storages())
        except Exception as exc:  # noqa: BLE001 - 退出清理失败不该阻断进程退出
            logger.warning("KGMemory finalize failed: %s", exc)

    def _ensure_initialized(self) -> None:
        """确保存储已初始化。"""
        if not self._initialized:
            async_runtime.run(self._rag.initialize_storages())
            self._initialized = True

    def ingest(self, execution) -> None:
        """将 WorkflowExecution 或 WorkflowRun 增量写入 LightRAG。"""
        self._ensure_initialized()

        # 1. 写入运行记录实体
        run_text = f"Run {execution.run_id}: {execution.intent.original_input}"
        async_runtime.run(self._rag.ainsert(run_text, ids=[f"run:{execution.run_id}"]))

        # 2. 写入步骤实体
        for step in execution.steps:
            step_text = f"Step {step.step_id}: {step.tool} {step.params}"
            async_runtime.run(
                self._rag.ainsert(step_text, ids=[f"step:{execution.run_id}:{step.step_id}"])
            )

        # 3. 写入输出实体（兼容 WorkflowRun，WorkflowExecution 无 outputs）
        outputs = getattr(execution, "outputs", None)
        if outputs:
            for out in outputs:
                out_text = f"Output {out.name}: {out.path}"
                async_runtime.run(
                    self._rag.ainsert(out_text, ids=[f"output:{execution.run_id}:{out.name}"])
                )

    def query_entities(self, query: str) -> list[str]:
        """实体检索：返回相关实体文本片段。"""
        self._ensure_initialized()
        param = QueryParam(mode="mix")
        result = async_runtime.run(self._rag.aquery(query, param=param))
        return [str(r) for r in result] if result else []

    def query_relations(self, entity: str) -> list[dict]:
        """关系查询：返回实体关系。"""
        self._ensure_initialized()
        param = QueryParam(mode="mix")
        result = async_runtime.run(
            self._rag.aquery(f"Relations of {entity}", param=param)
        )
        return [{"text": str(r)} for r in result] if result else []
