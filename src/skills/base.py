"""Skill 基类：定义技能接口与生命周期。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.skills.manifest import SkillMetadata


@dataclass
class SkillContext:
    """技能执行上下文。"""
    run_id: str
    params: dict[str, Any]
    artifacts: dict[str, Any]


class SkillBase(ABC):
    """技能基类：所有技能必须继承并实现 execute()。"""
    
    metadata: SkillMetadata
    
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "metadata"):
            raise TypeError(f"{cls.__name__} must define 'metadata' as SkillMetadata")
    
    @abstractmethod
    async def execute(self, context: SkillContext) -> dict[str, Any]:
        """执行技能核心逻辑，返回输出字典。"""
    
    async def setup(self, context: SkillContext) -> None:
        """可选：执行前初始化。"""
    
    async def teardown(self, context: SkillContext) -> None:
        """可选：执行后清理。"""
