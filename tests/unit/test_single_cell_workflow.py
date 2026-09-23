"""单细胞/空间分析工作流"""
from src.control.workflow_manager import WorkflowManager


class SCIntent:
    def parse(self, user_input, context=None):
        return {"type": "analysis", "analysis_type": "single_cell",
                "original_input": user_input}

    def extract_parameters(self, user_input):
        return {"input_files": ["scrna.csv"]}


class SpatialIntent:
    def parse(self, user_input, context=None):
        return {"type": "analysis", "analysis_type": "spatial",
                "original_input": user_input}

    def extract_parameters(self, user_input):
        return {"input_files": ["spatial_data"]}


class RecordingGen:
    def __init__(self):
        self.last = None

    def generate_code(self, analysis_type, params, method_context=None):
        self.last = {"type": analysis_type, "params": params}
        return f"#!/usr/bin/env Rscript\n# {analysis_type}"


class FakeExec:
    def __init__(self):
        self.codes = []

    def execute_code(self, code):
        self.codes.append(code)

        class R:
            returncode = 0

        return R()


def _wm(intent, require=False):
    gen = RecordingGen()
    wm = WorkflowManager(
        intent_parser=intent, knowledge_client=None,
        r_executor=FakeExec(), visualizer=None,
        r_script_generator=gen,
        require_script_confirmation=require,
    )
    return wm, gen


def test_single_cell_routes_and_output_names(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("c1,c2\nG1,1,2\n", encoding="utf-8")
    wm, gen = _wm(SCIntent(), require=False)
    ctx = {"downloaded_assets": [{"access_path": str(f)}]}
    result = wm.execute_workflow("做单细胞聚类", context=ctx)
    assert result["status"] == "success"
    assert result["analysis_type"] == "single_cell"
    assert gen.last["type"] == "single_cell"
    assert gen.last["params"]["output_file"].endswith(".sc_clusters.csv")
    assert gen.last["params"]["marker_file"].endswith(".sc_markers.csv")


def test_single_cell_demo_without_data(tmp_path):
    wm, _ = _wm(SCIntent(), require=False)
    result = wm.execute_workflow("做单细胞聚类")
    assert result["status"] == "success"
    assert "演示模式" in result["message"]


def test_single_cell_confirmation_flow(tmp_path):
    f = tmp_path / "m.csv"
    f.write_text("c1,c2\nG1,1,2\n", encoding="utf-8")
    wm, _ = _wm(SCIntent(), require=True)
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
    wm, gen = _wm(SpatialIntent(), require=False)
    ctx = {"downloaded_assets": [{"access_path": str(d)}]}
    result = wm.execute_workflow("分析空间转录组", context=ctx)
    assert result["status"] == "success"
    assert result["analysis_type"] == "spatial"
    assert gen.last["type"] == "spatial"
    assert gen.last["params"]["output_file"].endswith(".spatial_clusters.csv")
    assert gen.last["params"]["plot_file"].endswith(".spatial_plot.png")


def test_execute_confirmed_script_accepts_sc_and_spatial():
    wm, _ = _wm(SCIntent(), require=True)
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