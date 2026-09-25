"""Skill 动态加载器：支持热重载、命名空间隔离。"""
from __future__ import annotations

import importlib.util
import sys

from src.skills.base import SkillBase
from src.skills.registry import SkillRegistry


class SkillLoader:
    """技能动态加载器：支持热重载、命名空间隔离。"""
    
    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._cache: dict[str, type[SkillBase]] = {}
    
    def load(self, skill_name: str, version: str | None = None, unique_suffix: str | None = None) -> type[SkillBase]:
        cache_key = skill_name
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        manifest = self.registry.get(skill_name, version)
        if not manifest:
            raise ValueError(f"Skill not found: {skill_name}")
        
        skill_dir = self.registry.skills_dir / skill_name
        entry_point = manifest.metadata.entry_point
        module_path = skill_dir / f"{entry_point}.py"
        
        if not module_path.exists():
            raise FileNotFoundError(f"Entry point not found: {module_path}")
        
        # 使用唯一模块名避免缓存冲突
        module_name = f"skill_{skill_name}"
        if unique_suffix:
            module_name = f"{module_name}_{unique_suffix}"
        
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        
        # 查找 SkillBase 子类
        skill_class = None
        for obj in module.__dict__.values():
            if isinstance(obj, type) and issubclass(obj, SkillBase) and obj is not SkillBase:
                skill_class = obj
                break
        
        if skill_class is None:
            raise ValueError(f"No SkillBase subclass found in {module_path}")
        
        self._cache[skill_name] = skill_class
        return skill_class
    
    def reload(self, skill_name: str) -> type[SkillBase]:
        if skill_name in self._cache:
            del self._cache[skill_name]
        # 清除 sys.modules 缓存以强制重新加载
        module_name = f"skill_{skill_name}"
        if module_name in sys.modules:
            del sys.modules[module_name]
        # 使用时间戳后缀强制重新加载
        import time
        unique_suffix = str(int(time.time() * 1000000))
        return self.load(skill_name, unique_suffix=unique_suffix)
    
    def create_instance(self, skill_name: str, version: str | None = None, **kwargs):
        skill_class = self.load(skill_name, version)
        return skill_class(**kwargs)