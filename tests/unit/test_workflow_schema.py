"""Workflow JSON Schema 合法性与序列化往返测试。"""
import json

from src.schemas.workflow import (
    AnalysisType,
    IntentRecord,
    ParameterRecord,
    StepOutput,
    TerminalStatus,
    WorkflowExecution,
    WorkflowStep,
)


def test_terminal_status_enum_values():
    """TerminalStatus 枚举值必须与 TERMINAL_STATUSES 一致。"""
    expected = {
        "success",
        "error",
        "needs_confirmation",
        "needs_script_confirmation",
        "needs_input",
        "no_results",
    }
    actual = {s.value for s in TerminalStatus}
    assert actual == expected


def test_analysis_type_enum_values():
    """AnalysisType 枚举值必须与 SUPPORTED_ANALYSIS_TYPES 一致。"""
    expected = {"differential_expression", "single_cell", "spatial"}
    actual = {a.value for a in AnalysisType}
    assert actual == expected


def test_intent_record_valid_types():
    """IntentRecord 只接受合法的 intent 类型。"""
    # analysis 类型
    intent = IntentRecord(
        type="analysis",
        analysis_type="differential_expression",
        original_input="分析差异表达",
        confidence=0.9,
    )
    assert intent.type == "analysis"
    assert intent.analysis_type == "differential_expression"

    # fetch_data 类型
    intent = IntentRecord(
        type="fetch_data",
        analysis_type=None,
        original_input="下载 GSE123",
        confidence=0.8,
    )
    assert intent.type == "fetch_data"
    assert intent.analysis_type is None

    # knowledge_query 类型
    intent = IntentRecord(type="knowledge_query", original_input="TP53 是什么", confidence=0.9)
    assert intent.type == "knowledge_query"

    # general 类型
    intent = IntentRecord(type="general", original_input="你好", confidence=0.5)
    assert intent.type == "general"


def test_parameter_record_extraction():
    """ParameterRecord 记录提取的参数。"""
    params = ParameterRecord(
        input_files=["data/counts.csv"],
        genes=["TP53", "BRCA1"],
        sources=["geo", "kegg"],
        dataset_ids=["GSE123456"],
    )
    assert params.input_files == ["data/counts.csv"]
    assert params.genes == ["TP53", "BRCA1"]
    assert params.sources == ["geo", "kegg"]
    assert params.dataset_ids == ["GSE123456"]


def test_step_output_serialization():
    """StepOutput 序列化包含结果与错误信息。"""
    output = StepOutput(
        status="success",
        result={"result_file": "out.de_results.csv", "genes": 1000},
        error=None,
    )
    assert output.status == "success"
    assert output.result["genes"] == 1000
    assert output.error is None

    # 错误情况
    output = StepOutput(status="error", result=None, error="R script failed")
    assert output.status == "error"
    assert output.error == "R script failed"


def test_workflow_step_analysis():
    """Analysis 类型步骤记录。"""
    step = WorkflowStep(
        step_id="step-1",
        step_type="analysis",
        tool="run_analysis",
        params={"analysis_type": "differential_expression"},
        output=StepOutput(
            status="success",
            result={"result_file": "out.de_results.csv"},
        ),
    )
    assert step.step_type == "analysis"
    assert step.tool == "run_analysis"
    assert step.output is not None
    assert step.output.status == "success"


def test_workflow_step_fetch_data():
    """FetchData 类型步骤记录。"""
    step = WorkflowStep(
        step_id="step-2",
        step_type="fetch_data",
        tool="search_datasets",
        params={"query": "GSE123456"},
        output=StepOutput(
            status="needs_confirmation",
            result={"candidates": [{"asset_id": "GSE123456"}]},
        ),
    )
    assert step.step_type == "fetch_data"
    assert step.output.status == "needs_confirmation"


def test_workflow_step_knowledge_query():
    """KnowledgeQuery 类型步骤记录。"""
    step = WorkflowStep(
        step_id="step-3",
        step_type="knowledge_query",
        tool="query_knowledge",
        params={"query": "TP53 作用"},
        output=StepOutput(
            status="success",
            result={"response": "TP53 是抑癌基因", "references": []},
        ),
    )
    assert step.step_type == "knowledge_query"
    assert step.output.result["response"] == "TP53 是抑癌基因"


def test_workflow_execution_roundtrip():
    """WorkflowExecution 完整序列化往返。"""
    execution = WorkflowExecution(
        run_id="run-001",
        timestamp="2026-09-24T12:00:00Z",
        intent=IntentRecord(
            type="analysis",
            analysis_type="differential_expression",
            original_input="分析差异表达基因",
            confidence=1.0,
        ),
        parameters=ParameterRecord(
            input_files=["data/counts.csv"],
            genes=["TP53"],
        ),
        steps=[
            WorkflowStep(
                step_id="step-1",
                step_type="analysis",
                tool="run_analysis",
                params={"analysis_type": "differential_expression"},
                output=StepOutput(
                    status="success",
                    result={"result_file": "out.de_results.csv", "genes": 1000},
                ),
            ),
            WorkflowStep(
                step_id="step-2",
                step_type="analysis",
                tool="run_analysis",
                params={"analysis_type": "differential_expression"},
                output=StepOutput(
                    status="needs_script_confirmation",
                    result={"script": "R code..."},
                ),
            ),
        ],
        meta={"user_id": "user1", "session_id": "sess-001"},
    )

    # 序列化
    json_str = execution.model_dump_json()
    data = json.loads(json_str)

    # 验证关键字段
    assert data["run_id"] == "run-001"
    assert data["intent"]["analysis_type"] == "differential_expression"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["step_type"] == "analysis"
    assert data["steps"][1]["output"]["status"] == "needs_script_confirmation"

    # 反序列化
    execution2 = WorkflowExecution.model_validate_json(json_str)
    assert execution2.run_id == "run-001"
    assert execution2.intent.analysis_type == "differential_expression"
    assert len(execution2.steps) == 2
    assert execution2.steps[1].output.status == "needs_script_confirmation"


def test_workflow_execution_json_schema_export():
    """WorkflowExecution 支持 JSON Schema 导出。"""
    schema = WorkflowExecution.model_json_schema()
    assert "properties" in schema
    assert "run_id" in schema["properties"]
    assert "intent" in schema["properties"]
    assert "steps" in schema["properties"]
    assert "meta" in schema["properties"]

    # 验证 steps 引用 WorkflowStep 定义
    steps_schema = schema["properties"]["steps"]
    assert "items" in steps_schema
    assert "$ref" in steps_schema["items"]
    assert steps_schema["items"]["$ref"] == "#/$defs/WorkflowStep"

    # 验证 WorkflowStep 定义中的 step_type 引用 StepType enum
    wfs = schema.get("$defs", {}).get("WorkflowStep", {})
    assert "step_type" in wfs.get("properties", {})
    step_type_ref = wfs["properties"]["step_type"]
    assert "$ref" in step_type_ref
    assert step_type_ref["$ref"] == "#/$defs/StepType"

    # 验证 StepType enum 定义
    step_type_def = schema.get("$defs", {}).get("StepType", {})
    assert "enum" in step_type_def
    assert set(step_type_def["enum"]) == {"analysis", "fetch_data", "knowledge_query"}


def test_invalid_terminal_status_rejected():
    """非法 TerminalStatus 应被拒绝。"""
    from pydantic import ValidationError

    try:
        StepOutput(status="invalid_status", result=None)
    except ValidationError as e:
        assert "status" in str(e)
    else:
        assert False, "应抛出 ValidationError"


def test_invalid_analysis_type_rejected():
    """非法 AnalysisType 应被拒绝。"""
    from pydantic import ValidationError

    try:
        WorkflowStep(
            step_id="s1",
            step_type="invalid_type",
            tool="run_analysis",
            params={},
        )
    except ValidationError as e:
        assert "step_type" in str(e)
    else:
        assert False, "应抛出 ValidationError"