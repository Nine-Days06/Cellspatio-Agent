"""ModalRouter：任务分发 + 回退链测试。"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

from src.control.router import ModalRouter
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry


def test_router_dispatch_analysis():
    """测试分析任务分发到正确的 skill"""
    registry = SkillRegistry(Path("/tmp/skills"))
    loader = MagicMock(spec=SkillLoader)
    loader.load.return_value = MagicMock()
    skill_instance = AsyncMock()
    skill_instance.execute.return_value = {"result": "ok"}
    loader.create_instance.return_value = skill_instance

    router = ModalRouter(registry, loader)
    result = router.route("分析差异表达基因", context={"downloaded_assets": [], "script_approved": True})

    assert result["status"] == "success"
    assert result["modality"] == "analysis"
    assert result["skill"] == "differential_expression"


def test_router_executes_skill_lifecycle():
    """route() 应依次 await setup → execute → teardown 并回传 output。"""
    registry = SkillRegistry(Path("/tmp/skills"))
    loader = MagicMock(spec=SkillLoader)
    loader.load.return_value = MagicMock()
    skill_instance = AsyncMock()
    skill_instance.execute.return_value = {"result": "ok"}
    loader.create_instance.return_value = skill_instance

    router = ModalRouter(registry, loader)
    result = router.route("分析差异表达基因", context={"script_approved": True})

    assert result["status"] == "success"
    skill_instance.setup.assert_awaited_once()
    skill_instance.execute.assert_awaited_once()
    skill_instance.teardown.assert_awaited_once()
    assert result["output"] == {"result": "ok"}


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


def test_hint_returns_classification_without_loading_skill():
    """hint 仅分类+最佳实践，不加载 Skill（skill 不存在也不报错）。"""
    registry = SkillRegistry(Path("/tmp/skills"))
    loader = MagicMock(spec=SkillLoader)
    router = ModalRouter(registry, loader)
    hint = router.hint("帮我做差异表达分析")
    assert hint["modality"] == "analysis"
    assert hint["skill"] == "differential_expression"
    assert "best_practices" in hint


def test_hint_general_when_no_keyword_match():
    registry = SkillRegistry(Path("/tmp/skills"))
    loader = MagicMock(spec=SkillLoader)
    router = ModalRouter(registry, loader)
    hint = router.hint("随便聊聊天气")
    assert hint["modality"] == "general"
    assert hint["skill"] is None
    assert hint["best_practices"] is None
