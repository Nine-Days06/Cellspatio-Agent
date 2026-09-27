"""SkillBase 接口与生命周期测试。"""
import asyncio

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


class _RecordingSkill(SkillBase):
    metadata = SkillMetadata(
        name="test_skill",
        version="1.0.0",
        description="Test skill",
        author="test",
    )

    async def execute(self, context: SkillContext) -> dict:
        return {"result": "ok"}


class _FakeKGMemory:
    def __init__(self):
        self.records = []

    def ingest(self, execution):
        self.records.append(execution)


def test_teardown_records_execution():
    """teardown 自动记忆：构造鸭子记录并调用 kg_memory.ingest。"""
    fake = _FakeKGMemory()
    skill = _RecordingSkill(kg_memory=fake)
    ctx = SkillContext(
        run_id="skill-abc123",
        params={"user_input": "测试输入"},
        artifacts={"result": "ok"},
    )

    asyncio.run(skill.teardown(ctx))

    assert len(fake.records) == 1
    rec = fake.records[0]
    assert rec.run_id == "skill-abc123"
    assert "test_skill" in rec.intent.original_input
    assert rec.steps[0].step_id == "execute"
    assert rec.steps[0].tool == "test_skill"
    assert rec.steps[0].params == {"user_input": "测试输入"}
    assert rec.outputs[0].name == "result"
    assert rec.outputs[0].path == "ok"


def test_teardown_without_kg_memory_skips():
    """无 kg_memory 时 teardown 静默跳过，不抛异常。"""
    skill = _RecordingSkill()
    ctx = SkillContext(run_id="r", params={}, artifacts={})
    asyncio.run(skill.teardown(ctx))


def test_record_failure_is_swallowed():
    """KG 写入失败仅记日志，不阻断 teardown。"""

    class _BrokenKG:
        def ingest(self, execution):
            raise RuntimeError("kg down")

    skill = _RecordingSkill(kg_memory=_BrokenKG())
    ctx = SkillContext(run_id="r", params={}, artifacts={})
    asyncio.run(skill.teardown(ctx))  # 不应抛异常


def test_record_execution_with_real_kgmemory(tmp_path):
    """回归：asyncio.run 上下文中经 to_thread 调真实 ingest 不得触发嵌套 loop 错误。"""
    from src.control.kg_memory import KGMemory

    kg = KGMemory(working_dir=tmp_path / "kg")
    try:
        skill = _RecordingSkill(kg_memory=kg)
        ctx = SkillContext(
            run_id="skill-real-1",
            params={"user_input": "测试"},
            artifacts={"f": "x"},
        )
        asyncio.run(skill.teardown(ctx))
    finally:
        kg.close()