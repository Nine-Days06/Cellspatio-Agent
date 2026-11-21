"""stage_run 阶段收尾接缝测试。"""

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.analysis.r_executor import RExecutorError
from src.analysis.stage_run import (
    FixedStageRun,
    PreparedStage,
    executor_streams,
    finish_stage,
    run_fixed_stage,
    start_stage,
)


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


class _Executor:
    """记录脚本调用的假 R 执行器。"""

    def __init__(self, *, returncode: int = 0, tamper: bool = False) -> None:
        self.returncode = returncode
        self.tamper = tamper
        self.calls = 0

    def execute_script(self, script: str) -> SimpleNamespace:
        """执行一次并可选改写脚本，模拟运行期篡改。"""
        self.calls += 1
        if self.tamper:
            Path(script).write_text("tampered", encoding="utf-8")
        return SimpleNamespace(returncode=self.returncode, stdout="out", stderr="err")


def _plan(tmp_path: Path, **overrides: Any) -> FixedStageRun:
    """构造最小固定阶段计划。"""
    metadata = {"status": "failed"}
    values = {
        "directory": tmp_path / "stage",
        "metadata": metadata,
        "prepare": lambda: PreparedStage(source={"v": 1}, data="data"),
        "script": lambda prepared: f"# {prepared.data}",
        "check": lambda prepared: {"summary": prepared.data},
        "after": lambda prepared: {"v": 1},
        "returncode_error": "R 执行失败",
        "script_error": "脚本变化",
        "errors": (RExecutorError, OSError, ValueError, KeyError, TypeError),
        "fingerprint": _fingerprints,
    }
    values.update(overrides)
    return FixedStageRun(**values)


def test_run_fixed_stage_completes_and_records_artifacts(tmp_path: Path):
    """成功路径写入来源、汇总、状态与产物。"""
    plan = _plan(tmp_path, source_keys=("source_before", "source_after"))

    result = run_fixed_stage(plan, _Executor())

    assert result["status"] == "completed"
    assert result["source_before"] == {"v": 1}
    assert result["source_after"] == {"v": 1}
    assert result["summary"] == "data"
    assert set(result["artifacts"]) >= {"script.R", "stdout.txt", "stderr.txt"}
    assert json.loads((tmp_path / "stage" / "metadata.json").read_text(encoding="utf-8")) == result


@pytest.mark.parametrize(
    ("executor", "message"),
    [(_Executor(returncode=2), "R 执行失败"), (_Executor(tamper=True), "脚本变化")],
)
def test_run_fixed_stage_projects_failures(
    tmp_path: Path, executor: _Executor, message: str
) -> None:
    """returncode 与脚本篡改都落为 failed。"""
    plan = _plan(tmp_path)

    result = run_fixed_stage(plan, executor)

    assert result["status"] == "failed"
    assert result["error"] == message
    assert (tmp_path / "stage" / "metadata.json").is_file()


def test_run_fixed_stage_rejects_source_change(tmp_path: Path):
    """运行前后来源不同时保留 after 并失败。"""
    plan = _plan(
        tmp_path,
        source_keys=("source_before", "source_after"),
        source_error="来源变化",
        after=lambda prepared: {"v": 2},
    )

    result = run_fixed_stage(plan, _Executor())

    assert result["error"] == "来源变化"
    assert result["source_after"] == {"v": 2}


def test_run_fixed_stage_records_check_failure(tmp_path: Path):
    """产物校验异常同样进入 failed 收尾。"""

    def broken(prepared: PreparedStage) -> dict[str, Any]:
        raise ValueError("产物缺失")

    result = run_fixed_stage(_plan(tmp_path, check=broken), _Executor())

    assert result["status"] == "failed"
    assert result["error"] == "产物缺失"


def test_run_fixed_stage_projects_metadata_but_compares_full_source(tmp_path: Path):
    """投影只影响落盘来源，比较仍看完整准备/收尾结果。"""
    plan = _plan(
        tmp_path,
        source_keys=("source_before", "source_after"),
        source_projection=lambda checked: checked["source"],
        prepare=lambda: PreparedStage(source={"source": "a", "cells": 3}),
        after=lambda prepared: {"source": "a", "cells": 4},
        source_error="来源变化",
    )

    result = run_fixed_stage(plan, _Executor())

    assert result["source_before"] == "a"
    assert result["source_after"] == "a"
    assert result["error"] == "来源变化"


def test_run_fixed_stage_fixed_error_and_stderr(tmp_path: Path):
    """固定失败文案与异常细节写入本地 stderr。"""

    def broken() -> PreparedStage:
        raise ValueError("private detail")

    plan = _plan(tmp_path, prepare=broken, failure_error="固定失败")

    result = run_fixed_stage(plan, _Executor())

    assert result["error"] == "固定失败"
    assert "private detail" in (tmp_path / "stage" / "stderr.txt").read_text(encoding="utf-8")
