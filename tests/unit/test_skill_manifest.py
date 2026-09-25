"""SkillManifest Schema 合法性与序列化测试。"""
from src.skills.manifest import SkillDependency, SkillManifest, SkillMetadata


def test_skill_metadata_valid():
    meta = SkillMetadata(
        name="test_skill",
        version="1.0.0",
        description="Test skill",
        author="test",
        tags=["analysis", "r"],
        entry_point="main",
        schema={"input": {"type": "object"}, "output": {"type": "object"}}
    )
    assert meta.name == "test_skill"
    assert meta.version == "1.0.0"
    assert meta.tags == ["analysis", "r"]


def test_skill_dependency_resolution():
    dep = SkillDependency(name="dep_skill", version=">=1.0.0,<2.0.0", optional=False)
    assert dep.name == "dep_skill"
    assert dep.version == ">=1.0.0,<2.0.0"
    assert dep.optional is False


def test_manifest_serialization():
    manifest = SkillManifest(
        metadata=SkillMetadata(
            name="test_skill",
            version="1.0.0",
            description="Test skill",
            author="test",
            tags=["analysis"],
            entry_point="main",
            schema={"input": {"type": "object"}, "output": {"type": "object"}}
        ),
        dependencies=[],
        config_schema={}
    )
    json_str = manifest.model_dump_json()
    restored = SkillManifest.model_validate_json(json_str)
    assert restored.metadata.name == "test_skill"
    assert restored.metadata.version == "1.0.0"