"""脚本执行前人工确认（HITL）"""

from pathlib import Path

from src.control.workflow_manager import WorkflowManager
from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeExec, FakeGen, FakeIntent


def _make(require: bool) -> WorkflowManager:
    return WorkflowManager(
        intent_parser=FakeIntent(extract_params_return={"input_files": []}),
        knowledge_client=None,
        r_executor=FakeExec(track_codes=True),
        visualizer=None,
        r_script_generator=FakeGen(return_value="#!/usr/bin/env Rscript\n# generated"),
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
        "differential_expression",
        params,
        f'#!/usr/bin/env Rscript\n# user-approved\noutput_file <- "{params["output_file"]}"\n',
    )
    assert result["status"] == "success"
    assert "user-approved" in wm.r_executor.codes[0]
    assert result["repair_count"] == 0


def test_confirmed_script_uses_session_analysis_root(tmp_path):
    """人工确认执行必须沿用会话根，胶囊不能落全局 data/analysis。"""
    store = SessionStore()
    project_id = store.create_project("proj", str(tmp_path))
    session_id = store.create_session("s", project_id=project_id)
    wm = _make(require=True)
    wm.analysis_roots = AnalysisRoots(store, tmp_path)
    f = tmp_path / "c.csv"
    f.write_text("g,a,b\nG1,1,2\n", encoding="utf-8")
    out = tmp_path / "out.de_results.csv"

    result = wm.execute_confirmed_script(
        "differential_expression",
        {"input_file": str(f), "output_file": str(out)},
        f'#!/usr/bin/env Rscript\noutput_file <- "{out}"\n',
        session_id=session_id,
    )

    assert result["status"] == "success"
    capsule = Path(result["capsule_dir"])
    assert capsule.parent.parent.name == "analysis"
    assert capsule.parent.parent.parent.name == project_id
    assert "projects" in capsule.parts
