"""单细胞/空间分析工作流"""

from unittest.mock import Mock

import pytest

from src.control.workflow_manager import WorkflowManager
from tests.unit.fakes import FakeExec, FakeGen, FakeIntent


def _wm(intent, require=False):
    gen = FakeGen(return_value="# script", track_calls=True)
    wm = WorkflowManager(
        intent_parser=intent,
        knowledge_client=None,
        r_executor=FakeExec(track_codes=True),
        visualizer=None,
        r_script_generator=gen,
        require_script_confirmation=require,
    )
    return wm, gen


@pytest.mark.parametrize("approved", [False, True])
def test_single_cell_routes_to_fixed_qc(tmp_path, monkeypatch, approved):
    f = tmp_path / "m.csv"
    f.write_text("gene,c1,c2\nG1,0,2.0\n", encoding="utf-8")
    intent = FakeIntent(
        parse_return={"type": "analysis", "analysis_type": "single_cell", "original_input": ""}
    )
    wm, gen = _wm(intent, require=True)
    wm.methods_kb = Mock()
    wm.explainer = Mock()
    wm.code_repairer = Mock()
    qc = Mock(
        return_value={
            "status": "needs_confirmation",
            "analysis_type": "single_cell",
            "stage": "qc_review",
        }
    )
    monkeypatch.setattr("src.analysis.sc_qc.run_sc_qc", qc)
    result = wm.execute_workflow(
        "做单细胞聚类",
        {"downloaded_assets": [{"access_path": str(f)}], "script_approved": approved},
    )
    assert result["stage"] == "qc_review"
    assert qc.call_args.args[0] == str(f)
    assert gen.calls == []
    wm.methods_kb.query_context.assert_not_called()
    wm.explainer.explain.assert_not_called()
    wm.code_repairer.repair.assert_not_called()


@pytest.mark.parametrize("require", [False, True])
def test_single_cell_no_data_needs_input(tmp_path, require):
    sc_intent = FakeIntent(
        parse_return={"type": "analysis", "analysis_type": "single_cell", "original_input": ""},
        extract_params_return={"input_files": [str(tmp_path / "missing.csv")]},
    )
    wm, gen = _wm(sc_intent, require=require)
    result = wm.execute_workflow("做单细胞聚类")
    assert result["status"] == "needs_input"
    assert result["analysis_type"] == "single_cell"
    assert "未检测到数据文件" in result["message"]
    assert "script" not in result
    assert gen.calls == []
    assert wm.r_executor.codes == []


@pytest.mark.parametrize("require", [False, True])
@pytest.mark.parametrize(
    "csv_text",
    [
        "",
        "gene,c1,c2\n",
        "gene\nG1\n",
        "gene,c1,c2\nG1,1,\n",
        "gene,c1,c2\nG1,1,NA\n",
        "gene,c1,c2\nG1,1,NaN\n",
        "gene,c1,c2\nG1,1,Inf\n",
        "gene,c1,c2\nG1,1,-Inf\n",
        "gene,c1,c2\nG1,1,1e309\n",
        "gene,c1,c2\nG1,1,-1\n",
        "gene,c1,c2\nG1,1,1.5\n",
        "gene,c1,c2\nG1,1,1.00000000000000000001\n",
        "gene,c1,c2\nG1,1,not_a_count\n",
        "gene,c1,c2\nG1,1\n",
        "gene,c1,c2\nG1,1,2,3\n",
        "gene,c1,c1\nG1,1,2\n",
        "gene,c1,\nG1,1,2\n",
        "gene,c1,c2\n,1,2\n",
        "gene,c1,c2\nG1,1,2\nG1,3,4\n",
    ],
)
def test_single_cell_invalid_csv_stops_before_codegen(tmp_path, csv_text, require):
    f = tmp_path / "invalid.csv"
    f.write_text(csv_text, encoding="utf-8")
    wm, gen = _wm(FakeIntent(), require=require)
    wm.methods_kb = Mock()
    result = wm.run_analysis_for_agent(
        "single_cell", {"input_files": [str(f)]}, {"script_approved": True}
    )

    assert result["status"] == "error"
    assert result["analysis_type"] == "single_cell"
    assert "script" not in result
    assert gen.calls == []
    assert wm.r_executor.codes == []
    wm.methods_kb.query_context.assert_not_called()
    assert f.read_text(encoding="utf-8") == csv_text


def test_single_cell_directory_is_not_counts_csv(tmp_path):
    wm, gen = _wm(FakeIntent())
    result = wm.run_analysis_for_agent("single_cell", {"input_files": [str(tmp_path)]})

    assert result["status"] == "error"
    assert gen.calls == []
    assert wm.r_executor.codes == []


@pytest.mark.parametrize("has_invalid_file", [False, True])
def test_single_cell_rejection_is_recorded(tmp_path, has_invalid_file):
    f = tmp_path / "counts.csv"
    if has_invalid_file:
        f.write_text("gene,c1\nG1,0.5\n", encoding="utf-8")
    wm, _ = _wm(FakeIntent())
    wm.workflow_recorder = Mock()
    wm.workflow_recorder.start_execution.return_value = "run-input-check"

    result = wm.run_analysis_for_agent("single_cell", {"input_files": [str(f)]})

    recorded = wm.workflow_recorder.record_step.call_args.kwargs["output"]
    assert recorded == {"status": result["status"], "message": result["message"]}
    wm.workflow_recorder.finish_run.assert_called_once_with("run-input-check")


@pytest.mark.parametrize("params", [{}, {"input_file": "missing.csv"}])
def test_single_cell_old_script_cannot_bypass_qc(params):
    wm, gen = _wm(FakeIntent(), require=True)
    result = wm.execute_confirmed_script("single_cell", params, "# previously generated")
    assert result["status"] == "error"
    assert gen.calls == [] and wm.r_executor.codes == []
    assert (
        wm._finish_analysis("single_cell", params, "# bypass", None, {"script_approved": True})[
            "status"
        ]
        == "error"
    )


def test_spatial_routes(tmp_path):
    d = tmp_path / "spatial_run"
    d.mkdir()
    spatial_intent = FakeIntent(
        parse_return={"type": "analysis", "analysis_type": "spatial", "original_input": ""},
        extract_params_return={"input_files": ["spatial_data"]},
    )
    wm, gen = _wm(spatial_intent, require=False)
    ctx = {"downloaded_assets": [{"access_path": str(d)}]}
    result = wm.execute_workflow("分析空间转录组", context=ctx)
    assert result["status"] == "success"
    assert result["analysis_type"] == "spatial"
    assert gen.last["type"] == "spatial"
    assert gen.last["params"]["output_file"].endswith(".spatial_clusters.csv")
    assert gen.last["params"]["plot_file"].endswith(".spatial_plot.png")


def test_execute_confirmed_script_still_accepts_spatial():
    wm, _ = _wm(FakeIntent(), require=True)
    result = wm.execute_confirmed_script(
        "spatial", {"input_file": "d", "output_file": "e", "plot_file": "f"}, "# s"
    )
    assert result["status"] == "success"


def test_seurat_template_full_pipeline():
    from src.control.r_script_generator import RScriptGenerator

    code = RScriptGenerator().generate_code(
        "single_cell",
        {"input_file": "m.csv", "output_file": "sc.csv", "marker_file": "markers.csv"},
    )
    assert "Seurat" in code
    assert "FindClusters" in code
    assert "RunUMAP" in code
    assert "FindAllMarkers" in code
    assert "单细胞分析模板" not in code
    assert 'output_file <- "sc.csv"' in code
    assert 'marker_file <- "markers.csv"' in code
    assert "round(mat)" not in code
    assert "!is.numeric(counts)" in code
    assert "!is.finite(counts)" in code
    assert "counts < 0" in code
    assert "counts != floor(counts)" in code
    assert code.index("!is.finite(counts)") < code.index("CreateSeuratObject")
