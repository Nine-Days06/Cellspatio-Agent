"""Skill 清单与元数据定义。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillDependency(BaseModel):
    name: str
    version: str = "*"
    optional: bool = False


class SkillMetadata(BaseModel):
    name: str
    version: str
    description: str
    author: str
    tags: list[str] = Field(default_factory=list)
    entry_point: str = "main"
io_schema: dict[str, Any] = Field(default_factory=dict)


class SkillIO(BaseModel):
    name: str
    type: str
    description: str = ""
    required: bool = True


class SkillManifest(BaseModel):
    metadata: SkillMetadata
    dependencies: list[SkillDependency] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    inputs: list[SkillIO] = Field(default_factory=list)
    outputs: list[SkillIO] = Field(default_factory=list)