"""QC 产物验证、失败留证和禁止自动下游执行。"""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.analysis.r_executor import RExecutorError
from src.analysis.sc_qc import _read_evidence, qc_script, run_sc_qc


def evidence(directory):
    """写一组与微型 CSV 一致的本地证据。"""
    (directory / "features.csv").write_text(
        "feature_id,symbol,unique_name\nMT-X,MT-X,MT-X\nG1,G1,G1\n"
    )
    (directory / "cell_qc.csv").write_text(
        "barcode,nCount_RNA,nFeature_RNA,percent_mt,missing_reason\nc1,0,0,NA,zero_counts\nc2,4,2,25,\n"
    )
    (directory / "versions.txt").write_text("R 4.6.1\nSeurat 5.6.0\nMatrix 1.7.5\n")
    (directory / "qc.png").write_bytes(b"\x89PNG\r\n\x1a\n")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "counts.csv"
    path.write_text("gene,c1,c2\nMT-X,0,1\nG1,0,3\n")
    return path


def test_fixed_qc_stops_and_preserves_input(source, tmp_path):
    executor = Mock()

    def execute(script):
        from pathlib import Path

        evidence(Path(script).parent)
        return SimpleNamespace(returncode=0, stdout="done", stderr="warning")

    executor.execute_script.side_effect = execute
    result = run_sc_qc(str(source), executor, tmp_path / "runs")
    assert result["status"] == "needs_confirmation"
    assert result["stage"] == "qc_review" and "script" not in result
    qc = result["results"]["qc"]
    assert qc["tool_version"] == "1.0.0"
    assert qc["cells"] == 2 and qc["filtered"] is False
    assert qc["scientific_status"] == "unreviewed"
    assert qc["metrics"]["percent_mt"]["median"] == 25
    from pathlib import Path

    meta = json.loads(Path(result["results"]["qc_artifacts"]["metadata.json"]).read_text())
    assert meta["inputs_before"] == meta["inputs_after"]
    executor.execute_script.assert_called_once()
    executor.execute_code.assert_not_called()
    code = qc_script(str(source), tmp_path)
    for forbidden in (
        "CreateSeuratObject",
        "NormalizeData",
        "FindClusters",
        "FindAllMarkers",
        "RunUMAP",
    ):
        assert forbidden not in code


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "barcode",
        "mapping",
        "nan",
        "false_zero",
        "reason",
        "versions",
        "png",
        "truncated",
        "extra",
    ],
)
def test_rejects_bad_evidence(source, tmp_path, change):
    evidence(tmp_path)
    if change == "missing":
        (tmp_path / "cell_qc.csv").unlink()
    elif change in ("barcode", "nan", "false_zero", "reason", "truncated", "extra"):
        path = tmp_path / "cell_qc.csv"
        old, new = {
            "barcode": ("c2", "c1"),
            "nan": (",4,2,", ",NaN,2,"),
            "false_zero": ("NA,zero_counts", "0,zero_counts"),
            "reason": ("zero_counts", "unknown"),
            "truncated": ("c2,4,2,25,", "c2,4,2"),
            "extra": ("c2,4,2,25,", "c2,4,2,25,,extra"),
        }[change]
        path.write_text(path.read_text().replace(old, new))
    else:
        path = (
            tmp_path
            / {"mapping": "features.csv", "versions": "versions.txt", "png": "qc.png"}[change]
        )
        path.write_text("bad")
    with pytest.raises((ValueError, OSError)):
        _read_evidence(tmp_path, source)


@pytest.mark.parametrize("failure", ["missing_output", "timeout", "r_error", "changed_input"])
def test_failure_leaves_logs_without_retry(source, tmp_path, failure):
    executor = Mock()

    def execute(script):
        if failure == "timeout":
            try:
                raise subprocess.TimeoutExpired(
                    "Rscript", 300, output=b"partial", stderr=b"timeout"
                )
            except subprocess.TimeoutExpired as exc:
                raise RExecutorError("timeout") from exc
        if failure == "r_error":
            raise RExecutorError("failure", "partial", "bad R")
        if failure == "changed_input":
            source.write_text(source.read_text().replace(",3", ",4"))
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    executor.execute_script.side_effect = execute
    result = run_sc_qc(str(source), executor, tmp_path / "runs")
    assert result["status"] == "error" and "qc" not in result["results"]
    artifacts = result["results"]["qc_artifacts"]
    assert {"script.R", "metadata.json", "stdout.txt", "stderr.txt"} <= artifacts.keys()
    if failure in ("timeout", "r_error"):
        from pathlib import Path

        assert Path(artifacts["stdout.txt"]).read_text() == "partial"
        assert Path(artifacts["stderr.txt"]).read_text() == (
            "timeout" if failure == "timeout" else "bad R"
        )
    executor.execute_script.assert_called_once()


def test_qc_recorder_does_not_snapshot_or_ingest(tmp_path, monkeypatch):
    from src.control.workflow_recorder import WorkflowRecorder

    snapshot = Mock()
    monkeypatch.setattr("src.control.snapshot_manager.SnapshotManager", snapshot)
    kg = Mock()
    recorder = WorkflowRecorder(str(tmp_path / "wrroc"), tmp_path, kg)
    run_id = recorder.start_execution(
        {"type": "analysis", "analysis_type": "single_cell", "original_input": "QC"}, {}, "QC", {}
    )
    assert recorder.finish_run(run_id).is_file()
    snapshot.assert_not_called()
    kg.ingest.assert_not_called()
