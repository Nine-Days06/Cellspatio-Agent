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


def test_io_schema_field():
    """io_schema 是 SkillMetadata 正规字段（修复缩进脱落回归）。"""
    meta = SkillMetadata(
        name="test_skill", version="1.0.0", description="Test", author="test"
    )
    assert meta.io_schema == {}
    restored = SkillMetadata.model_validate({
        "name": "test_skill", "version": "1.0.0", "description": "Test",
        "author": "test",
        "io_schema": {"input": {"type": "object"}},
    })
    assert restored.io_schema == {"input": {"type": "object"}}
