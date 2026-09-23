"""脚本执行前人工确认（HITL）"""
from src.control.workflow_manager import WorkflowManager


class FakeIntent:
    def parse(self, user_input, context=None):
        return {"type": "analysis", "analysis_type": "differential_expression",
                "original_input": user_input}

    def extract_parameters(self, user_input):
        return {"input_files": ["counts.csv"]}


class FakeGen:
    def generate_code(self, analysis_type, params, method_context=None):
        return "#!/usr/bin/env Rscript\n# generated"


class FakeExec:
    def __init__(self):
        self.codes = []

    def execute_code(self, code):
        self.codes.append(code)

        class R:
            returncode = 0

        return R()


def _make(require: bool) -> WorkflowManager:
    return WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(),
        visualizer=None,
        r_script_generator=FakeGen(),
        require_script_confirmation=require,
    )


def test_confirmation_required_returns_pending_script(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("g,a,b\nG1,1,2\n", encoding="utf-8")
    wm = _make(require=True)
    context = {"downloaded_assets": [{"access_path": str(f)}]}
    result = wm.execute_workflow("做差异表达", context=context)
    assert result["status"] == "needs_script_confirmation"
    assert "#!/usr/bin/env Rscript" in result["script"]
    assert result["analysis_type"] == "differential_expression"
    assert result["params"]["input_file"] == str(f)
    assert wm.r_executor.codes == []


def test_confirmation_disabled_runs_immediately(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("g,a,b\nG1,1,2\n", encoding="utf-8")
    wm = _make(require=False)
    context = {"downloaded_assets": [{"access_path": str(f)}]}
    result = wm.execute_workflow("做差异表达", context=context)
    assert result["status"] == "success"
    assert len(wm.r_executor.codes) == 1


def test_execute_confirmed_script_runs_repair_loop(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("g,a,b\nG1,1,2\n", encoding="utf-8")
    wm = _make(require=True)
    params = {
        "input_file": str(f),
        "output_file": str(f.with_suffix(".de_results.csv")),
    }
    result = wm.execute_confirmed_script(
        "differential_expression", params,
        "#!/usr/bin/env Rscript\n# user-approved",
    )
    assert result["status"] == "success"
    assert "user-approved" in wm.r_executor.codes[0]
    assert result["repair_count"] == 0