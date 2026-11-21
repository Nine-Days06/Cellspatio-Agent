"""stage_run 阶段收尾接缝测试。"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from src.analysis.r_executor import RExecutorError
from src.analysis.stage_run import executor_streams, finish_stage, start_stage


def _fingerprints(paths: list[Path]) -> list[dict[str, object]]:
    return [{"path": str(path.resolve()), "bytes": path.stat().st_size} for path in paths]


def test_start_stage_creates_directory_and_refuses_existing(tmp_path: Path):
    directory = tmp_path / "qc"
    started = start_stage(directory)
    assert directory.is_dir()
    assert started <= time.perf_counter()
    with pytest.raises(FileExistsError):
        start_stage(directory)


def test_executor_streams_uses_timeout_cause_and_decodes_bytes():
    timeout = subprocess.TimeoutExpired("Rscript", 300, output=b"partial\xff", stderr=b"kill\xfe")
    error = RExecutorError("timed out")
    error.__cause__ = timeout
    assert executor_streams(error) == ("partial\ufffd", "kill\ufffd")
    assert executor_streams(RExecutorError("failed", b"out", "err")) == ("out", "err")
    assert executor_streams(ValueError("x")) == ("", "")


def test_finish_stage_writes_logs_and_metadata_without_artifacts(tmp_path: Path):
    directory = tmp_path / "filter"
    started = start_stage(directory)
    metadata = {"tool": "sc_filter", "status": "completed", "message": "中文"}

    finish_stage(
        directory, metadata, started, b"out\xff", "err", artifacts=False, fingerprints=_fingerprints
    )

    assert (directory / "stdout.txt").read_text(encoding="utf-8") == "out\ufffd"
    assert (directory / "stderr.txt").read_text(encoding="utf-8") == "err"
    assert "artifacts" not in metadata
    assert metadata["elapsed_seconds"] >= 0
    assert json.loads((directory / "metadata.json").read_text(encoding="utf-8")) == metadata
    assert '"tool": "sc_filter"' in (directory / "metadata.json").read_text(encoding="utf-8")
    assert "中文" in (directory / "metadata.json").read_text(encoding="utf-8")


def test_finish_stage_registers_artifacts_after_logs(tmp_path: Path):
    directory = tmp_path / "pca"
    started = start_stage(directory)
    metadata = {"status": "error"}

    finish_stage(
        directory, metadata, started, "out", "err", artifacts=True, fingerprints=_fingerprints
    )

    assert set(metadata["artifacts"]) == {"stdout.txt", "stderr.txt"}
    assert metadata["artifacts"]["stdout.txt"]["path"].endswith("stdout.txt")
    assert metadata["artifacts"]["stdout.txt"]["bytes"] == 3
    assert "artifacts" in json.loads((directory / "metadata.json").read_text(encoding="utf-8"))


def test_finish_stage_requires_fingerprints_and_propagates_errors(tmp_path: Path):
    directory = tmp_path / "cluster"
    started = start_stage(directory)
    with pytest.raises(ValueError):
        finish_stage(directory, {}, started, "", "", artifacts=True)
    assert not (directory / "metadata.json").exists()

    def broken(paths: list[Path]) -> list[dict[str, object]]:
        raise OSError("fingerprint failed")

    with pytest.raises(OSError):
        finish_stage(directory, {}, started, "", "", artifacts=True, fingerprints=broken)
    assert not (directory / "metadata.json").exists()
