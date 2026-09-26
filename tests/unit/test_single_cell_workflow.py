"""单细胞/空间分析工作流"""
from src.control.workflow_manager import WorkflowManager
from tests.unit.fakes import FakeExec, FakeGen, FakeIntent


def _wm(intent, require=False):
    gen = FakeGen(return_value="# script", track_calls=True)
    wm = WorkflowManager(
        intent_parser=intent, knowledge_client=None,
        r_executor=FakeExec(track_codes=True), visualizer=None,
        r_script_generator=gen,
        require_script_confirmation=require,
    )
    return wm, gen


def test_single_cell_routes_and_output_names(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("c1,c2\nG1,1,2\n", encoding="utf-8")
    sc_intent = FakeIntent(
        parse_return={"type": "analysis", "analysis_type": "single_cell", "original_input": ""},
        extract_params_return={"input_files": ["scrna.csv"]},
    )
    wm, gen = _wm(sc_intent, require=False)
    ctx = {"downloaded_assets": [{"access_path": str(f)}]}
    result = wm.execute_workflow("做单细胞聚类", context=ctx)
    assert result["status"] == "success"
    assert result["analysis_type"] == "single_cell"
    assert gen.last["type"] == "single_cell"
    assert gen.last["params"]["output_file"].endswith(".sc_clusters.csv")
    assert gen.last["params"]["marker_file"].endswith(".sc_markers.csv")


def test_single_cell_no_data_generates_script_for_confirmation(tmp_path):
    sc_intent = FakeIntent(
        parse_return={"type": "analysis", "analysis_type": "single_cell", "original_input": ""},
        extract_params_return={"input_files": ["scrna.csv"]},
    )
    wm, _ = _wm(sc_intent, require=False)
    result = wm.execute_workflow("做单细胞聚类")
    assert result["status"] == "needs_script_confirmation"
    assert "未检测到数据文件" in result["message"]
    assert result["script"]


def test_single_cell_confirmation_flow(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("c1,c2\nG1,1,2\n", encoding="utf-8")
    sc_intent = FakeIntent(
        parse_return={"type": "analysis", "analysis_type": "single_cell", "original_input": ""},
        extract_params_return={"input_files": ["scrna.csv"]},
    )
    wm, _ = _wm(sc_intent, require=True)
    ctx = {"downloaded_assets": [{"access_path": str(f)}]}
    result = wm.execute_workflow("做单细胞聚类", context=ctx)
    assert result["status"] == "needs_script_confirmation"
    assert result["analysis_type"] == "single_cell"

    result2 = wm.execute_confirmed_script(
        "single_cell", result["params"], result["script"],
        method_context=result.get("method_context"),
    )
    assert result2["status"] == "success"
    assert len(wm.r_executor.codes) == 1


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


def test_execute_confirmed_script_accepts_sc_and_spatial():
    sc_intent = FakeIntent(
        parse_return={"type": "analysis", "analysis_type": "single_cell", "original_input": ""},
        extract_params_return={"input_files": ["scrna.csv"]},
    )
    wm, _ = _wm(sc_intent, require=True)
    r1 = wm.execute_confirmed_script("single_cell", {"input_file": "a", "output_file": "b", "marker_file": "c"}, "# s")
    assert r1["status"] == "success"
    r2 = wm.execute_confirmed_script("spatial", {"input_file": "d", "output_file": "e", "plot_file": "f"}, "# s")
    assert r2["status"] == "success"


def test_seurat_template_full_pipeline():
    from src.control.r_script_generator import RScriptGenerator

    code = RScriptGenerator().generate_code(
        "single_cell",
        {"input_file": "m.csv", "output_file": "sc.csv",
         "marker_file": "markers.csv"},
    )
    assert "Seurat" in code
    assert "FindClusters" in code
    assert "RunUMAP" in code
    assert "FindAllMarkers" in code
    assert "单细胞分析模板" not in code
    assert 'output_file <- "sc.csv"' in code
    assert 'marker_file <- "markers.csv"' in code