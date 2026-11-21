"""完成态阶段产物统一校验的用例。"""

import json
from pathlib import Path
from typing import Any

import pytest

from src.analysis.evidence import fingerprints
from src.analysis.stage_verify import CompletedStageCheck, verify_completed

PROTOCOL = {"version": 1}


def _check(directory: Path, *, exact: bool = True, **overrides: Any) -> CompletedStageCheck:
    """构造已登记完成的阶段校验计划。"""
    directory.mkdir()
    (directory / "a.txt").write_text("a", encoding="utf-8")
    (directory / "script.R").write_text("# script", encoding="utf-8")
    names = ["a.txt", "script.R"] + ([] if exact else ["stdout.txt"])
    if not exact:
        (directory / "stdout.txt").write_text("", encoding="utf-8")
    artifacts = {
        r["path"].split("/")[-1].split("\\")[-1]: r
        for r in fingerprints([directory / name for name in names])
    }
    meta: dict[str, Any] = {
        "status": "completed",
        "returncode": 0,
        "protocol": PROTOCOL,
        "binding_sha256": "b",
        "source_before": {"v": 1},
        "source_after": {"v": 1},
        "cells": 3,
        "artifacts": artifacts,
    }
    (directory / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    attempt = {
        "status": "completed",
        "tool_version": "1.0",
        "protocol": json.dumps(PROTOCOL),
        "result": meta,
    }
    values = {
        "directory": directory,
        "expected_directory": directory.resolve(),
        "attempt": attempt,
        "tool_version": "1.0",
        "protocol": PROTOCOL,
        "required_fields": {
            "status": "completed",
            "returncode": 0,
            "protocol": PROTOCOL,
            "binding_sha256": "b",
            "source_before": {"v": 1},
            "source_after": {"v": 1},
        },
        "required_files": tuple(names),
        "exact_files": exact,
        "summary": lambda: {"cells": 3},
        "script": lambda: "# script",
        "result_error": "result",
        "directory_error": "directory",
        "metadata_path_error": "metadata path",
        "files_error": "files",
        "artifact_error": "artifact",
        "identity_error": "identity",
        "summary_error": "summary",
        "script_error": "script",
    }
    values.update(overrides)
    return CompletedStageCheck(**values)


def test_verify_completed_accepts_exact_and_subset(tmp_path: Path) -> None:
    """精确白名单与允许额外产物的子集都能通过。"""
    exact_summary, exact_ids = verify_completed(_check(tmp_path / "exact"))
    subset_summary, subset_ids = verify_completed(_check(tmp_path / "subset", exact=False))

    assert exact_summary == subset_summary == {"cells": 3}
    assert set(exact_ids) == {"a.txt", "script.R"}
    assert set(subset_ids) == {"a.txt", "script.R", "stdout.txt"}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "attempt": {
                    "status": "failed",
                    "tool_version": "1.0",
                    "protocol": "{}",
                    "result": {},
                }
            },
            "result",
        ),
        ({"expected_directory": Path("/other")}, "directory"),
        ({"required_files": ("a.txt",), "exact_files": True}, "artifact"),
    ],
)
def test_verify_completed_rejects_contract_drift(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    """完成态、目录与白名单优先于后续校验。"""
    check = _check(tmp_path / "case", **overrides)
    with pytest.raises(ValueError, match=message):
        verify_completed(check)


def test_verify_completed_checks_files_identity_summary_and_script(tmp_path: Path) -> None:
    """文件缺失、身份变化、汇总与脚本各自报出对应文案。"""
    directory = tmp_path / "case"
    base = _check(directory)
    (directory / "a.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        verify_completed(base)

    with pytest.raises(ValueError, match="summary"):
        verify_completed(_check(tmp_path / "summary", summary=lambda: {"cells": 4}))
    with pytest.raises(ValueError, match="script"):
        verify_completed(_check(tmp_path / "script", script=lambda: "# changed"))

    missing = _check(tmp_path / "missing")
    (missing.directory / "a.txt").unlink()
    with pytest.raises(ValueError, match="files"):
        verify_completed(missing)
