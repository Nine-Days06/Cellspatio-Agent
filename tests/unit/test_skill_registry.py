"""SkillRegistry 功能测试。"""
import tempfile
from pathlib import Path

from src.skills.manifest import SkillDependency, SkillManifest, SkillMetadata
from src.skills.registry import SkillRegistry


def _make_skill(name="test_skill", version="1.0.0"):
    return SkillManifest(
        metadata=SkillMetadata(name=name, version=version, description="d", author="t", tags=[], entry_point="main", schema={}),
        dependencies=[],
        config_schema={}
    )


def test_registry_register_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        registry = SkillRegistry(Path(tmp))
        skill = _make_skill("test_skill", "1.0.0")
        registry.register(skill)
        got = registry.get("test_skill")
        assert got.metadata.name == "test_skill"
        assert got.metadata.version == "1.0.0"


def test_registry_version_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        registry = SkillRegistry(Path(tmp))
        registry.register(_make_skill("skill_a", "1.0.0"))
        registry.register(_make_skill("skill_a", "2.0.0"))
        # 默认获取最新版本
        got = registry.get("skill_a")
        assert got.metadata.version == "2.0.0"
        # 显式指定版本
        got = registry.get("skill_a", version="1.0.0")
        assert got.metadata.version == "1.0.0"


def test_registry_dependency_resolution():
    with tempfile.TemporaryDirectory() as tmp:
        registry = SkillRegistry(Path(tmp))
        dep = _make_skill("dep_skill", "1.0.0")
        skill = _make_skill("skill_with_dep", "1.0.0")
        skill.dependencies = [SkillDependency(name="dep_skill", version="1.0.0")]
        registry.register(dep)
        registry.register(skill)
        resolved = registry.resolve_dependencies("skill_with_dep")
        assert len(resolved) == 1
        assert resolved[0].metadata.name == "dep_skill"