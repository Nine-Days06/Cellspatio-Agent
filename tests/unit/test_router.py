"""ModalRouter：任务分发 + 回退链测试。"""
from pathlib import Path
from unittest.mock import MagicMock

from src.control.router import ModalRouter
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry


def test_router_dispatch_analysis():
    """测试分析任务分发到正确的 skill"""
    registry = SkillRegistry(Path("/tmp/skills"))
    loader = MagicMock(spec=SkillLoader)
    loader.load.return_value = MagicMock()
    loader.create_instance.return_value = MagicMock()
    
    router = ModalRouter(registry, loader)
    result = router.route("分析差异表达基因", context={"downloaded_assets": [], "script_approved": True})
    
    assert result["status"] == "success"
    assert result["modality"] == "analysis"
    assert result["skill"] == "differential_expression"


def test_router_fallback_chain():
    registry = SkillRegistry(Path("/tmp/skills"))
    loader = MagicMock(spec=SkillLoader)
    router = ModalRouter(registry, loader)
    
    # 未知任务 → general
    result = router.route("未知任务 xyz", context={})
    assert result["modality"] == "general"
    assert result["status"] == "success"
    assert result["message"] is not None


def test_router_hitl_interrupt():
    registry = SkillRegistry(Path("/tmp/skills"))
    loader = MagicMock(spec=SkillLoader)
    router = ModalRouter(registry, loader)
    
    # 模拟 HITL 状态
    result = router.route("分析差异表达基因", context={"script_approved": False})
    assert result["status"] == "needs_script_confirmation"