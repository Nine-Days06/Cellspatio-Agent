"""模态路由器：任务分类 → Skill 分发 → 回退链 + 自动记忆。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from src.control.classifier import TaskClassifier
from src.control.kg_memory import KGMemory
from src.skills.base import SkillContext
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class ModalRouter:
    """模态感知路由器：分类 → 分发 → 回退链 + 自动记忆。"""
    
    def __init__(self, registry: SkillRegistry, loader: SkillLoader, kg_memory: KGMemory | None = None):
        self.classifier = TaskClassifier()
        self.registry = registry
        self.loader = loader
        self.kg_memory = kg_memory
    
    def route(self, user_input: str, context: dict | None = None) -> dict[str, Any]:
        """路由入口：分类 → 查询最佳实践 → 调用 Skill → 自动记忆 → 处理 HITL/回退。"""
        context = context or {}
        classification = self.classifier.classify(user_input)
        modality = classification["modality"]
        skill_name = classification["skill"]
        
        if modality == "general" or skill_name is None:
            return {"status": "success", "modality": "general", "message": "收到，请问有什么可以帮您？"}
        
        # HITL 检查：需要脚本确认但未批准
        if skill_name and not context.get("script_approved"):
            return {"status": "needs_script_confirmation", "skill": skill_name, "message": "需要确认脚本"}
        
        # 加载并执行 Skill
        try:
            self.loader.load(skill_name)
            
            # P3d: 查询最佳实践注入上下文
            best_practices = None
            if self.kg_memory and hasattr(self, '_query_best_practices'):
                best_practices = self._query_best_practices(skill_name, user_input)
            
            # 创建实例并注入 KGMemory
            skill_instance = self.loader.create_instance(skill_name, kg_memory=self.kg_memory)

            # P3c/P3d: 生命周期挂点 setup → execute → teardown（teardown 内自动记忆）
            skill_context = SkillContext(
                run_id=context.get("run_id") or f"skill-{uuid4().hex[:8]}",
                params={"user_input": user_input},
                artifacts={},
                best_practices=best_practices,
            )

            async def _lifecycle() -> Any:
                await skill_instance.setup(skill_context)
                output = await skill_instance.execute(skill_context)
                if isinstance(output, dict):
                    skill_context.artifacts.update(output)
                await skill_instance.teardown(skill_context)
                return output

            output = asyncio.run(_lifecycle())

            return {
                "status": "success",
                "modality": modality,
                "skill": skill_name,
                "message": f"已分发到 {skill_name}",
                "best_practices": best_practices,
                "output": output,
            }
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "modality": modality, "skill": skill_name, "message": str(e)}
    
    def _query_best_practices(self, skill_name: str, user_input: str) -> str | None:
        """查询知识图谱获取该技能的最佳实践。"""
        if not self.kg_memory:
            return None
        try:
            # 组合技能名和用户输入查询
            query = f"{skill_name}: {user_input}"
            entities = self.kg_memory.query_entities(query)
            if entities:
                return "\n".join(entities[:3])
        except Exception as e:  # noqa: BLE001 - 查询失败降级为无提示
            logger.debug("best_practices query failed: %s", e)
        return None

    def hint(self, user_input: str) -> dict[str, Any]:
        """轻量路由提示：仅分类 + 最佳实践查询，不加载 Skill。

        每轮 tool-calling 都调（AgentRuntime._route_hint），结果注入 system prompt。
        与 route() 分工：route() 会真正 load 技能并跑 setup → execute → teardown，
        只在确定要跑分析时由 AgentRuntime._skill_plan 调一次；
        skills/ 下已有 5 个真实技能，load 不会失败。
        """
        classification = self.classifier.classify(user_input)
        modality = classification["modality"]
        skill = classification["skill"]
        if modality == "general" or skill is None:
            return {"modality": "general", "skill": None, "best_practices": None}
        return {
            "modality": modality,
            "skill": skill,
            "best_practices": self._query_best_practices(skill, user_input),
        }