"""Skill 动态加载器：支持热重载、命名空间隔离。"""
from __future__ import annotations

import importlib.util
import sys
import time
import types

from src.skills.base import SkillBase
from src.skills.registry import SkillRegistry


class SkillLoader:
    """技能动态加载器：支持热重载、命名空间隔离。"""
    
    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._cache: dict[str, type[SkillBase]] = {}
    
    def load(self, skill_name: str, version: str | None = None, unique_suffix: str | None = None) -> type[SkillBase]:
        cache_key = f"{skill_name}_{unique_suffix}" if unique_suffix else skill_name
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
        
        # 读取源码并编译执行，完全绕过 import 缓存
        source = module_path.read_text(encoding="utf-8")
        module_name = f"skill_{skill_name}"
        if unique_suffix:
            module_name = f"{module_name}_{unique_suffix}"
        
        # 创建全新的模块命名空间
        module = types.ModuleType(module_name)
        module.__file__ = str(module_path)
        module.__name__ = module_name
        
        # 编译并执行源码
        code = compile(source, str(module_path), 'exec')
        exec(code, module.__dict__)
        
        # 查找 SkillBase 子类
        skill_class = None
        for obj in module.__dict__.values():
            if isinstance(obj, type) and issubclass(obj, SkillBase) and obj is not SkillBase:
                skill_class = obj
                break
        
        if skill_class is None:
            raise ValueError(f"No SkillBase subclass found in {module_path}")
        
        cache_key = f"{skill_name}_{unique_suffix}" if unique_suffix else skill_name
        self._cache[cache_key] = skill_class
        return skill_class
    
    def reload(self, skill_name: str) -> type[SkillBase]:
        # 清除所有相关缓存
        to_delete_cache = [k for k in self._cache if k.startswith(skill_name)]
        for k in to_delete_cache:
            del self._cache[k]
        
        # 使用时间戳后缀强制重新加载（完全绕过 import 缓存）
        unique_suffix = str(int(time.time() * 1000000))
        return self.load(skill_name, unique_suffix=unique_suffix)
    
    def create_instance(self, skill_name: str, version: str | None = None, **kwargs):
        skill_class = self.load(skill_name, version)
        return skill_class(**kwargs)