"""SkillBase 接口与生命周期测试。"""
from src.skills.base import SkillBase, SkillContext
from src.skills.manifest import SkillMetadata


def test_skill_base_interface():
    class TestSkill(SkillBase):
        metadata = SkillMetadata(
            name="test_skill",
            version="1.0.0",
            description="Test skill",
            author="test"
        )
        
        async def execute(self, context: SkillContext) -> dict:
            return {"result": "ok"}
    
    skill = TestSkill()
    assert hasattr(skill, "metadata")
    assert hasattr(skill, "execute")
    assert callable(skill.execute)


def test_skill_context_passing():
    from src.skills.base import SkillContext
    ctx = SkillContext(run_id="test", params={"x": 1}, artifacts={})
    assert ctx.run_id == "test"
    assert ctx.params["x"] == 1