"""Analysis Capsule 单测"""

from datetime import datetime, timezone
from types import SimpleNamespace

import src.analysis.capsule as capsule
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


def test_export_same_timestamp_does_not_overwrite(tmp_path, monkeypatch):
    """同一微秒导出两次也不能复用目录。"""
    fixed = datetime(2026, 11, 22, tzinfo=timezone.utc)
    monkeypatch.setattr(capsule, "datetime", SimpleNamespace(now=lambda tz=None: fixed))
    first = export_analysis_capsule("q1", {}, {}, "", {}, out_root=tmp_path)
    second = export_analysis_capsule("q2", {}, {}, "", {}, out_root=tmp_path)

    assert first != second
    assert (first / "question.txt").read_text(encoding="utf-8") == "q1"
    assert (second / "question.txt").read_text(encoding="utf-8") == "q2"
