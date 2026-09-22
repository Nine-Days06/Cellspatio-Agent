"""Analysis Capsule 单测"""
from src.analysis.capsule import export_analysis_capsule


def test_export_creates_files(tmp_path):
    out = export_analysis_capsule(
        out_root=tmp_path,
        question="做差异表达",
        intent={"type": "analysis", "analysis_type": "differential_expression"},
        params={"input_file": "counts.csv"},
        script_code="# R script",
        results={"returncode": 0, "output_file": "x.csv"},
    )
    assert (out / "question.txt").read_text(encoding="utf-8") == "做差异表达"
    assert (out / "script.R").read_text(encoding="utf-8") == "# R script"
    assert (out / "intent.json").exists()
    assert (out / "meta.json").exists()
    meta = (out / "meta.json").read_text(encoding="utf-8")
    assert "counts.csv" in meta
