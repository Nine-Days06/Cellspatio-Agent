"""模态路由器：任务分类 → Skill 分发 → 回退链。"""
from __future__ import annotations

from typing import Any

from src.control.classifier import TaskClassifier
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry


class ModalRouter:
    """模态感知路由器：分类 → 分发 → 回退链。"""
    
    def __init__(self, registry: SkillRegistry, loader: SkillLoader):
        self.classifier = TaskClassifier()
        self.registry = registry
        self.loader = loader
    
    def route(self, user_input: str, context: dict | None = None) -> dict[str, Any]:
        """路由入口：分类 → 调用 Skill → 处理 HITL/回退。"""
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
            self.loader.create_instance(skill_name)
            # 这里简化：实际应调用 skill.execute()
            return {
                "status": "success",
                "modality": modality,
                "skill": skill_name,
                "message": f"已分发到 {skill_name}"
            }
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "modality": modality, "skill": skill_name, "message": str(e)}