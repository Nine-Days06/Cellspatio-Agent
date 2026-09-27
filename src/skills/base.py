"""Skill 基类：定义技能接口与生命周期 + 自动记忆。"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

from src.control.kg_memory import KGMemory
from src.skills.manifest import SkillMetadata

logger = logging.getLogger(__name__)


@dataclass
class SkillContext:
    """技能执行上下文。"""
    run_id: str
    params: dict[str, Any]
    artifacts: dict[str, Any]
    # P3d: 注入的最佳实践上下文
    best_practices: Optional[str] = None


class SkillBase(ABC):
    """技能基类：所有技能必须继承并实现 execute()。"""
    
    metadata: SkillMetadata
    
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "metadata"):
            raise TypeError(f"{cls.__name__} must define 'metadata' as SkillMetadata")
    
    def __init__(self, kg_memory: Optional[KGMemory] = None, **kwargs):
        """初始化技能，可选注入 KGMemory 用于自动记忆。"""
        self.kg_memory = kg_memory
        super().__init__(**kwargs)
    
    @abstractmethod
    async def execute(self, context: SkillContext) -> dict[str, Any]:
        """执行技能核心逻辑，返回输出字典。"""
    
    async def setup(self, context: SkillContext) -> None:
        """可选：执行前初始化。"""
    
    async def teardown(self, context: SkillContext) -> None:
        """可选：执行后清理 + 自动记忆。"""
        if self.kg_memory:
            await self._record_execution(context)
    
    async def _record_execution(self, context: SkillContext) -> None:
        """将技能执行记录写入知识图谱（best-effort：失败仅记日志，不阻断执行）。"""
        try:
            record = SimpleNamespace(
                run_id=context.run_id,
                intent=SimpleNamespace(
                    original_input=f"skill:{self.metadata.name} {context.params}"
                ),
                steps=[
                    SimpleNamespace(
                        step_id="execute",
                        tool=self.metadata.name,
                        params=context.params,
                    )
                ],
                outputs=[
                    SimpleNamespace(name=str(k), path=str(v))
                    for k, v in (context.artifacts or {}).items()
                ],
            )
            # KGMemory 自持独立事件循环，须经工作线程调用以避免嵌套 loop RuntimeError
            await asyncio.to_thread(self.kg_memory.ingest, record)
        except Exception as e:  # noqa: BLE001 - 记忆写入失败不阻断技能执行
            logger.warning("skill execution record failed: %s", e)
    
    @classmethod
    async def query_best_practices(cls, kg_memory: KGMemory, task_description: str) -> Optional[str]:
        """查询知识图谱获取最佳实践。"""
        if not kg_memory:
            return None
        try:
            entities = kg_memory.query_entities(task_description)
            if entities:
                return "\n".join(entities[:3])
        except Exception:
            pass
        return None
