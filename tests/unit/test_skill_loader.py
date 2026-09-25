"""SkillLoader 动态加载测试。"""
import asyncio
import tempfile
from pathlib import Path

from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry


def test_loader_loads_skill_class():
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.json").write_text('{"metadata": {"name": "test_skill", "version": "1.0.0", "description": "d", "author": "t", "tags": [], "entry_point": "main", "schema": {}}, "dependencies": [], "config_schema": {}, "inputs": [], "outputs": []}')
        (skill_dir / "main.py").write_text("""
from src.skills.base import SkillBase, SkillContext
class TestSkill(SkillBase):
    metadata = {"name": "test_skill", "version": "1.0.0", "description": "d", "author": "t", "tags": [], "entry_point": "main", "schema": {}}
    async def execute(self, context: SkillContext):
        return {"output": context.params.get("x", 0) * 2}
""")
        registry = SkillRegistry(Path(tmp))
        loader = SkillLoader(registry)
        skill_class = loader.load("test_skill")
        assert skill_class is not None
        assert issubclass(skill_class, object)


def test_loader_cache():
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.json").write_text('{"metadata": {"name": "test_skill", "version": "1.0.0", "description": "d", "author": "t", "tags": [], "entry_point": "main", "schema": {}}, "dependencies": [], "config_schema": {}, "inputs": [], "outputs": []}')
        (skill_dir / "main.py").write_text("""
from src.skills.base import SkillBase, SkillContext
class TestSkill(SkillBase):
    metadata = {"name": "test_skill", "version": "1.0.0", "description": "d", "author": "t", "tags": [], "entry_point": "main", "schema": {}}
    async def execute(self, context):
        return {"result": "ok"}
""")
        registry = SkillRegistry(Path(tmp))
        loader = SkillLoader(registry)
        
        # 第一个加载
        skill_class1 = loader.load("test_skill")
        # 第二次加载应返回缓存
        skill_class2 = loader.load("test_skill")
        assert skill_class1 is skill_class2


def test_loader_reload():
    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.json").write_text('{"metadata": {"name": "test_skill", "version": "1.0.0", "description": "d", "author": "t", "tags": [], "entry_point": "main", "schema": {}}, "dependencies": [], "config_schema": {}, "inputs": [], "outputs": []}')
        (skill_dir / "main.py").write_text("""
from src.skills.base import SkillBase, SkillContext
class TestSkill(SkillBase):
    metadata = {"name": "test_skill", "version": "1.0.0", "description": "d", "author": "t", "tags": [], "entry_point": "main", "schema": {}}
    async def execute(self, context):
        return {"result": "v1"}
""")
        registry = SkillRegistry(Path(tmp))
        loader = SkillLoader(registry)
        
        skill_class1 = loader.load("test_skill")
        # 修改源码
        (skill_dir / "main.py").write_text("""
from src.skills.base import SkillBase, SkillContext
class TestSkill(SkillBase):
    metadata = {"name": "test_skill", "version": "2.0.0", "description": "d", "author": "t", "tags": [], "entry_point": "main", "schema": {}}
    async def execute(self, context):
        return {"result": "v2"}
""")
        # 重新加载
        skill_class2 = loader.reload("test_skill")
        assert skill_class2 is not skill_class1
        instance = skill_class2()
        result = asyncio.run(instance.execute(None))
        assert result == {"result": "v2"}