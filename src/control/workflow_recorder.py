"""WorkflowRecorder：自动记录工作流执行的 intent/params/steps/outputs。

设计：
- 使用 contextvars 实现运行级隔离（同一进程并发多 run_id）
- 内存记录，显式 export_json/export_jsonl 导出
- 线程安全：contextvars 天然隔离，单 run_id 串行记录
- 完成时自动落盘到 WRROCStore
"""
from __future__ import annotations

import contextvars
import uuid
from pathlib import Path
from typing import Any

from src.control.wrroc_store import WRROCStore
from src.schemas.workflow import (
    AnalysisType,
    IntentRecord,
    IntentType,
    ParameterRecord,
    StepOutput,
    StepType,
    TerminalStatus,
    WorkflowExecution,
    WorkflowRun,
    WorkflowStep,
)

# 运行级上下文变量：run_id -> WorkflowExecution
_run_context: contextvars.ContextVar[dict[str, WorkflowExecution] | None] = contextvars.ContextVar(
    "_run_context", default=None
)


def _get_context() -> dict[str, WorkflowExecution]:
    """获取或初始化上下文字典。"""
    ctx = _run_context.get()
    if ctx is None:
        ctx = {}
        _run_context.set(ctx)
    return ctx


class WorkflowRecorder:
    """工作流执行记录器。

    用法：
        recorder = WorkflowRecorder()
        run_id = recorder.start_execution(intent, params, user_input, context)
        recorder.record_intent(run_id, ...)
        recorder.record_parameters(run_id, ...)
        recorder.record_step(run_id, ...)
        recorder.finish_run(run_id)  # 自动落盘到 WRROCStore
    """

    def __init__(self, wrroc_base_dir: str = ".wrroc"):
        self.store = WRROCStore(Path(wrroc_base_dir))

    @classmethod
    def get_context(cls) -> dict[str, WorkflowExecution]:
        """获取上下文字典（供外部获取 run_id 列表）。"""
        ctx = _run_context.get()
        if ctx is None:
            ctx = {}
            _run_context.set(ctx)
        return ctx

    def start_execution(
        self,
        intent: dict[str, Any],
        parameters: dict[str, Any],
        user_input: str,
        context: dict[str, Any],
        run_id: str | None = None,
    ) -> str:
        """开始新的执行记录，返回 run_id。"""
        run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"

        # 创建初始记录
        intent_record = IntentRecord(**intent)
        param_record = ParameterRecord(**parameters)
        execution = WorkflowExecution(
            run_id=run_id,
            intent=intent_record,
            parameters=param_record,
            meta={"user_input": user_input, "context": context},
        )

        # 写入上下文
        ctx = _get_context()
        ctx[run_id] = execution
        return run_id

    def _get_execution(self, run_id: str) -> WorkflowExecution | None:
        ctx = _get_context()
        return ctx.get(run_id)

    def _require_execution(self, run_id: str) -> WorkflowExecution:
        exec_ = self._get_execution(run_id)
        if exec_ is None:
            raise KeyError(f"Run ID not found: {run_id}")
        return exec_

    def record_intent(
        self,
        run_id: str,
        intent_type: str,
        analysis_type: str | None = None,
        original_input: str = "",
        confidence: float = 1.0,
    ) -> None:
        """记录/更新意图解析结果。"""
        exec_ = self._require_execution(run_id)
        exec_.intent = IntentRecord(
            type=IntentType(intent_type),
            analysis_type=AnalysisType(analysis_type) if analysis_type else None,
            original_input=original_input,
            confidence=confidence,
        )

    def record_parameters(
        self,
        run_id: str,
        input_files: list[str] | None = None,
        genes: list[str] | None = None,
        sources: list[str] | None = None,
        dataset_ids: list[str] | None = None,
    ) -> None:
        """记录/更新参数提取结果。"""
        exec_ = self._require_execution(run_id)
        exec_.parameters = ParameterRecord(
            input_files=input_files or [],
            genes=genes or [],
            sources=sources or [],
            dataset_ids=dataset_ids or [],
        )

    def record_step(
        self,
        run_id: str,
        step_id: str,
        step_type: str,
        tool: str,
        params: dict[str, Any],
        output: dict[str, Any] | None = None,
    ) -> None:
        """记录单步执行结果。"""
        exec_ = self._require_execution(run_id)

        step_output = None
        if output:
            step_output = StepOutput(
                status=TerminalStatus(output.get("status", "success")),
                result=output.get("result"),
                error=output.get("error"),
            )

        step = WorkflowStep(
            step_id=step_id,
            step_type=StepType(step_type),
            tool=tool,
            params=params,
            output=step_output,
        )
        exec_.add_step(step)

    def finish_run(self, run_id: str) -> Path:
        """结束记录，返回完整 WorkflowExecution，并自动落盘到 WRROCStore。"""
        exec_ = self._require_execution(run_id)
        # 转换为 WorkflowRun 并持久化
        run = self._to_workflow_run(exec_)
        return self.store.persist(run)

    def _to_workflow_run(self, exec_: WorkflowExecution) -> WorkflowRun:
        """将 WorkflowExecution 转换为 WorkflowRun 供 WRROCStore 使用。"""
        from src.schemas.workflow import (
            StepOutput,
            StepType,
            TerminalStatus,
            WorkflowInput,
            WorkflowIntent,
            WorkflowRun,
            WorkflowStep,
        )

        # 转换 steps
        steps = []
        for step in exec_.steps:
            step_output = None
            if step.output:
                step_output = StepOutput(
                    status=TerminalStatus(step.output.status.value),
                    result=step.output.result,
                    error=step.output.error,
                )
            steps.append(WorkflowStep(
                step_id=step.step_id,
                step_type=StepType(step.step_type.value),
                tool=step.tool,
                params=step.params,
                output=step_output,
            ))

        return WorkflowRun(
            run_id=exec_.run_id,
            intent=WorkflowIntent(
                type=exec_.intent.type,
                analysis_type=exec_.intent.analysis_type,
                original_input=exec_.intent.original_input,
                confidence=exec_.intent.confidence,
            ),
            params=exec_.parameters.model_dump(),
            input=WorkflowInput(
                user_input=exec_.meta.get("user_input", ""),
                context=exec_.meta.get("context", {}),
            ),
            steps=steps,
            outputs=[],  # 由具体 workflow 方法在 finish_run 前设置
            meta=exec_.meta,
        )

    def get_record(self, run_id: str) -> WorkflowExecution | None:
        """获取执行记录（未 finish 也可获取）。"""
        return self._get_execution(run_id)

    def export_json(self, run_id: str, indent: int = 2) -> str:
        """导出完整记录为 JSON 字符串。"""
        exec_ = self._require_execution(run_id)
        return exec_.to_json(indent=indent)

    def export_jsonl(self, run_id: str) -> str:
        """导出 JSONL（每行一个步骤，含 run_id 与 timestamp）。"""
        exec_ = self._require_execution(run_id)
        return exec_.to_jsonl()