"""Skill 注册表：注册、发现、加载、版本管理、依赖解析。"""
from __future__ import annotations

import json
from pathlib import Path

from src.skills.manifest import SkillManifest


class SkillRegistry:
    """技能注册表：管理技能清单、版本、依赖。"""
    
    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, list[dict]] = {}
        self._load_all()
    
    def _load_all(self):
        self._index = {}
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            manifest_file = skill_dir / "manifest.json"
            if manifest_file.exists():
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                manifest = SkillManifest.model_validate(data)
                self._index.setdefault(manifest.metadata.name, []).append(manifest.model_dump())
    
    def register(self, manifest) -> None:
        skill_dir = self.skills_dir / manifest.metadata.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = skill_dir / "manifest.json"
        manifest_file.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        self._index.setdefault(manifest.metadata.name, []).append(manifest.model_dump())
    
    def get(self, name: str, version: str | None = None):
        if name not in self._index:
            return None
        versions = self._index[name]
        if version is None:
            # 返回最新版本
            latest = max(versions, key=lambda v: v["metadata"]["version"])
            return SkillManifest.model_validate(latest)
        for v in versions:
            if v["metadata"]["version"] == version:
                return SkillManifest.model_validate(v)
        return None
    
    def resolve_dependencies(self, skill_name: str, version: str | None = None) -> list[dict]:
        skill = self.get(skill_name, version)
        if not skill:
            return []
        deps = []
        for dep in skill.dependencies:
            dep_skill = self.get(dep.name, dep.version)
            if dep_skill:
                deps.append(dep_skill)
        return deps
    
    def list_skills(self) -> list[dict]:
        result = []
        for versions in self._index.values():
            latest = max(versions, key=lambda v: v["metadata"]["version"])
            result.append(latest["metadata"])
        return result