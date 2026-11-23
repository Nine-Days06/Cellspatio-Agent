"""模态路由器：完整技能执行与只读分析方案两个明确入口。"""

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
    """模态路由器：完整技能执行与只读分析方案读取。"""

    def __init__(
        self, registry: SkillRegistry, loader: SkillLoader, kg_memory: KGMemory | None = None
    ):
        self.classifier = TaskClassifier()
        self.registry = registry
        self.loader = loader
        self.kg_memory = kg_memory

    def route(self, user_input: str, context: dict | None = None) -> dict[str, Any]:
        """完整技能入口：分类 → HITL → 加载 Skill → setup/execute/teardown。"""
        context = context or {}
        dispatched_skill = self._classify_skill(user_input, require_analysis=False)
        if dispatched_skill is None:
            return {
                "status": "success",
                "modality": "general",
                "message": "收到，请问有什么可以帮您？",
            }
        modality, skill_name = dispatched_skill

        # HITL 检查：需要脚本确认但未批准
        if not context.get("script_approved"):
            return {
                "status": "needs_script_confirmation",
                "skill": skill_name,
                "message": "需要确认脚本",
            }

        # 加载并执行 Skill
        try:
            self.loader.load(skill_name)

            # P3d: 查询最佳实践注入上下文
            best_practices = None
            if self.kg_memory and hasattr(self, "_query_best_practices"):
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

    def read_analysis_plan(
        self, user_input: str, *, best_practices: str | None = None
    ) -> dict[str, Any] | None:
        """执行技能方案构建器，不运行 setup/teardown 或发布执行事实。"""
        analysis_skill = self._classify_skill(user_input, require_analysis=True)
        if analysis_skill is None:
            return None
        _, skill_name = analysis_skill

        self.loader.load(skill_name)
        skill_instance = self.loader.create_instance(skill_name)
        context = SkillContext(
            run_id=f"plan-{uuid4().hex[:8]}",
            params={"user_input": user_input},
            artifacts={},
            best_practices=best_practices,
        )
        output = asyncio.run(skill_instance.execute(context))
        return output if isinstance(output, dict) else None

    def _classify_skill(self, user_input: str, *, require_analysis: bool) -> tuple[str, str] | None:
        """分类可分发技能；方案读取时只允许分析模态，不加载技能。"""
        classification = self.classifier.classify(user_input)
        modality = classification["modality"]
        skill_name = classification["skill"]
        if modality == "general" or skill_name is None:
            return None
        if require_analysis and modality != "analysis":
            return None
        return modality, skill_name

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
        与 route() 分工：route() 运行完整技能生命周期并发布执行事实；
        AgentRuntime 读取分析方案时使用 read_analysis_plan()。
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
