"""WorkflowRecorder 行为测试。"""
from src.control.workflow_recorder import WorkflowRecorder
from src.schemas.workflow import (
    IntentRecord,
    ParameterRecord,
)


def _make_intent() -> "IntentRecord":
    from src.schemas.workflow import IntentRecord
    return IntentRecord(
        type="analysis",
        analysis_type="differential_expression",
        original_input="分析差异表达基因",
        confidence=1.0,
    )


def _make_params() -> "ParameterRecord":
    from src.schemas.workflow import ParameterRecord
    return ParameterRecord(
        input_files=["data/counts.csv"],
        genes=["TP53"],
    )


def test_recorder_start_execution_creates_record():
    """start_execution 创建 WorkflowExecution 记录。"""
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test", "confidence": 1.0},
        parameters={"input_files": ["data.csv"]},
        user_input="测试输入",
        context={"history": []},
    )
    assert run_id is not None
    record = recorder.get_record(run_id)
    assert record is not None
    assert record.run_id == run_id
    assert record.intent.type.value == "analysis"
    assert record.intent.analysis_type.value == "differential_expression"
    assert record.parameters.input_files == ["data.csv"]
    assert record.steps == []


def test_recorder_records_intent():
    """record_intent 更新 intent 字段。"""
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "general", "original_input": "hi"},
        parameters={},
        user_input="hi",
        context={},
    )
    recorder.record_intent(
        run_id,
        intent_type="analysis",
        analysis_type="differential_expression",
        original_input="分析差异表达",
        confidence=0.95,
    )
    record = recorder.get_record(run_id)
    assert record.intent.type.value == "analysis"
    assert record.intent.analysis_type.value == "differential_expression"
    assert record.intent.confidence == 0.95


def test_recorder_records_parameters():
    """record_parameters 更新参数。"""
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test"},
        parameters={},
        user_input="test",
        context={},
    )
    recorder.record_parameters(run_id, input_files=["data/counts.csv"], genes=["TP53", "BRCA1"])
    record = recorder.get_record(run_id)
    assert record.parameters.input_files == ["data/counts.csv"]
    assert record.parameters.genes == ["TP53", "BRCA1"]


def test_recorder_records_step():
    """record_step 追加步骤记录。"""
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test"},
        parameters={},
        user_input="test",
        context={},
    )
    recorder.record_step(
        run_id,
        step_id="step-1",
        step_type="analysis",
        tool="run_analysis",
        params={"analysis_type": "differential_expression"},
        output={"status": "success", "result": {"result_file": "out.de_results.csv"}},
    )
    record = recorder.get_record(run_id)
    assert len(record.steps) == 1
    step = record.steps[0]
    assert step.step_id == "step-1"
    assert step.step_type.value == "analysis"
    assert step.tool == "run_analysis"
    assert step.output is not None
    assert step.output.status.value == "success"


def test_recorder_records_multiple_steps():
    """多步骤正确追加并保持顺序。"""
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test"},
        parameters={},
        user_input="test",
        context={},
    )
    recorder.record_step(run_id, "step-1", "analysis", "run_analysis", {}, output={"status": "needs_script_confirmation"})
    recorder.record_step(run_id, "step-2", "analysis", "run_analysis", {}, output={"status": "success", "result": {"file": "out.csv"}})
    record = recorder.get_record(run_id)
    assert len(record.steps) == 2
    assert record.steps[0].step_id == "step-1"
    assert record.steps[1].step_id == "step-2"
    assert record.steps[0].output.status.value == "needs_script_confirmation"
    assert record.steps[1].output.status.value == "success"


def test_recorder_finish_run_returns_path():
    """finish_run 返回 WRROC 存储路径。"""
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test"},
        parameters={},
        user_input="test",
        context={},
    )
    recorder.record_step(run_id, "step-1", "analysis", "run_analysis", {}, output={"status": "success"})
    path = recorder.finish_run(run_id)
    assert path.name == "workflow.json"
    assert path.parent.name == run_id


def test_recorder_export_json():
    """export_json 返回完整 JSON 字符串。"""
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test"},
        parameters={"input_files": ["data.csv"]},
        user_input="test",
        context={},
    )
    recorder.record_step(run_id, "s1", "analysis", "run_analysis", {}, output={"status": "success", "result": {"file": "out.csv"}})
    recorder.finish_run(run_id)
    json_str = recorder.export_json(run_id)
    assert "run_id" in json_str
    assert "differential_expression" in json_str
    assert "run_analysis" in json_str


def test_recorder_export_jsonl():
    """export_jsonl 返回每行一个步骤的 JSONL。"""
    import json
    recorder = WorkflowRecorder()
    run_id = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test"},
        parameters={},
        user_input="test",
        context={},
    )
    recorder.record_step(run_id, "s1", "analysis", "run_analysis", {"analysis_type": "de"}, output={"status": "success", "result": {"file": "out1.csv"}})
    recorder.record_step(run_id, "s2", "analysis", "run_analysis", {}, output={"status": "success", "result": {"file": "out2.csv"}})
    recorder.finish_run(run_id)
    jsonl = recorder.export_jsonl(run_id)
    lines = jsonl.strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        data = json.loads(line)
        assert data["run_id"] == run_id
        assert "step" in data


def test_recorder_get_nonexistent_returns_none():
    """获取不存在的 run_id 返回 None。"""
    recorder = WorkflowRecorder()
    assert recorder.get_record("nonexistent") is None


def test_recorder_contextvar_isolation():
    """不同 run_id 的记录互不干扰（contextvars 隔离）。"""
    recorder = WorkflowRecorder()
    run_id1 = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "differential_expression", "original_input": "test1"},
        parameters={},
        user_input="test1",
        context={},
    )
    run_id2 = recorder.start_execution(
        intent={"type": "analysis", "analysis_type": "single_cell", "original_input": "test2"},
        parameters={},
        user_input="test2",
        context={},
    )
    recorder.record_step(run_id1, "s1", "analysis", "run_analysis", {}, output={"status": "success"})
    recorder.record_step(run_id2, "s1", "analysis", "run_analysis", {}, output={"status": "needs_input"})
    r1 = recorder.get_record(run_id1)
    r2 = recorder.get_record(run_id2)
    assert r1.intent.analysis_type.value == "differential_expression"
    assert r2.intent.analysis_type.value == "single_cell"
    assert len(r1.steps) == 1
    assert len(r2.steps) == 1
    assert r1.steps[0].output.status.value == "success"
    assert r2.steps[0].output.status.value == "needs_input"