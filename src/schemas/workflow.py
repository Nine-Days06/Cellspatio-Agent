"""Workflow JSON Schema：Pydantic 模型定义完整执行记录结构。

设计目标：
- 覆盖 intent/params/steps/outputs 完整链路
- 与 Phase 1 TERMINAL_STATUSES / SUPPORTED_ANALYSIS_TYPES 对齐
- 支持 JSON/JSONL 序列化与 JSON Schema 导出
- 供 WorkflowRecorder、WRROCStore、Replay 复用
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── 枚举定义 ──────────────────────────────────────────────────────

class TerminalStatus(str, Enum):
    """工具/步骤终态状态。与 Phase 1 TERMINAL_STATUSES 完全一致。"""
    SUCCESS = "success"
    ERROR = "error"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_SCRIPT_CONFIRMATION = "needs_script_confirmation"
    NEEDS_INPUT = "needs_input"
    NO_RESULTS = "no_results"


class AnalysisType(str, Enum):
    """支持的分析类型。与 Phase 1 SUPPORTED_ANALYSIS_TYPES 完全一致。"""
    DIFFERENTIAL_EXPRESSION = "differential_expression"
    SINGLE_CELL = "single_cell"
    SPATIAL = "spatial"


class StepType(str, Enum):
    """工作流步骤类型。对应三个工具。"""
    ANALYSIS = "analysis"
    FETCH_DATA = "fetch_data"
    KNOWLEDGE_QUERY = "knowledge_query"


class IntentType(str, Enum):
    """意图类型。"""
    ANALYSIS = "analysis"
    FETCH_DATA = "fetch_data"
    KNOWLEDGE_QUERY = "knowledge_query"
    GENERAL = "general"


# ── 记录模型 ──────────────────────────────────────────────────────

class IntentRecord(BaseModel):
    """意图解析记录。"""
    type: IntentType
    analysis_type: AnalysisType | None = None
    original_input: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class ParameterRecord(BaseModel):
    """参数提取记录。对应 IntentParser.extract_parameters() 返回值。"""
    input_files: list[str] = Field(default_factory=list)
    genes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    dataset_ids: list[str] = Field(default_factory=list)


class StepOutput(BaseModel):
    """单步执行输出。"""
    status: TerminalStatus
    result: dict[str, Any] | None = None
    error: str | None = None


class WorkflowStep(BaseModel):
    """单个工作流步骤记录。"""
    step_id: str
    step_type: StepType
    tool: Literal["run_analysis", "search_datasets", "query_knowledge"]
    params: dict[str, Any] = Field(default_factory=dict)
    output: StepOutput | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class WorkflowExecution(BaseModel):
    """完整工作流执行记录：intent → params → steps → outputs。"""
    run_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    intent: IntentRecord
    parameters: ParameterRecord
    steps: list[WorkflowStep] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def add_step(self, step: WorkflowStep) -> None:
        """追加步骤记录。"""
        self.steps.append(step)

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串。"""
        return self.model_dump_json(indent=indent)

    def to_jsonl(self) -> str:
        """序列化为 JSONL（每行一个步骤，含 run_id 与 timestamp）。"""
        lines = []
        base = {"run_id": self.run_id, "timestamp": self.timestamp.isoformat()}
        for step in self.steps:
            line = {**base, "step": step.model_dump(mode="json")}
            lines.append(json.dumps(line, ensure_ascii=False))
        return "\n".join(lines)

    @classmethod
    def from_json(cls, json_str: str) -> WorkflowExecution:
        """从 JSON 字符串反序列化。"""
        return cls.model_validate_json(json_str)


# 延迟导入 json，避免循环
import json


# ── 兼容 WRROCStore 所需的 WorkflowRun 等模型 ─────────────────────────

class WorkflowIntent(BaseModel):
    """意图记录。"""
    type: IntentType
    analysis_type: AnalysisType | None = None
    original_input: str
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class WorkflowInput(BaseModel):
    """输入记录。"""
    user_input: str
    context: dict[str, Any] = Field(default_factory=dict)


class WorkflowOutput(BaseModel):
    """输出记录。"""
    name: str
    path: str
    type: Literal["csv", "json", "txt", "png", "rds", "other"]
    meta: dict[str, Any] = Field(default_factory=dict)


class WorkflowRun(BaseModel):
    """完整工作流运行记录：与 WorkflowExecution 字段兼容，字段名对齐 WRROCStore 需求。"""
    run_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    intent: WorkflowIntent
    params: dict[str, Any] = Field(default_factory=dict)
    input: WorkflowInput = Field(default_factory=WorkflowInput)
    steps: list[WorkflowStep] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def add_step(self, step: WorkflowStep) -> None:
        self.steps.append(step)

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def to_jsonl(self) -> str:
        lines = []
        base = {"run_id": self.run_id, "timestamp": self.timestamp.isoformat()}
        for step in self.steps:
            line = {**base, "step": step.model_dump(mode="json")}
            lines.append(json.dumps(line, ensure_ascii=False))
        return "\n".join(lines)

    @classmethod
    def from_json(cls, json_str: str) -> "WorkflowRun":
        return cls.model_validate_json(json_str)