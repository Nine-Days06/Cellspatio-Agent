"""完成态阶段产物的统一校验：目录、metadata、协议、白名单、身份、汇总与脚本。"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.analysis.evidence import fingerprints


@dataclass(frozen=True)
class CompletedStageCheck:
    """一个已完成阶段的校验计划；差异只由字段表达。"""

    directory: Path
    expected_directory: Path
    attempt: dict[str, Any]
    tool_version: str
    protocol: dict[str, Any] | None
    required_fields: dict[str, Any]
    required_files: tuple[str, ...]
    exact_files: bool
    summary: Callable[[], dict[str, Any]]
    script: Callable[[], str]
    result_error: str
    directory_error: str
    metadata_path_error: str
    files_error: str
    artifact_error: str
    identity_error: str
    summary_error: str
    script_error: str


def verify_completed(plan: CompletedStageCheck) -> tuple[dict[str, Any], dict[str, Any]]:
    """校验已完成阶段并返回产物汇总与文件身份。"""
    directory = plan.directory.resolve()
    if directory != plan.expected_directory:
        raise ValueError(plan.directory_error)
    meta_path = directory / "metadata.json"
    if meta_path.resolve().parent != directory:
        raise ValueError(plan.metadata_path_error)
    meta = json.loads(meta_path.read_bytes())
    if (
        plan.attempt["status"] != "completed"
        or plan.attempt["tool_version"] != plan.tool_version
        or (plan.protocol is not None and json.loads(plan.attempt["protocol"]) != plan.protocol)
        or meta != plan.attempt["result"]
        or any(meta.get(k) != v for k, v in plan.required_fields.items())
    ):
        raise ValueError(plan.result_error)
    names = set(meta["artifacts"])
    if plan.exact_files and set(plan.required_files) != names:
        raise ValueError(plan.artifact_error)
    if not plan.exact_files and not set(plan.required_files) <= names:
        raise ValueError(plan.artifact_error)
    paths = [directory / name for name in meta["artifacts"]]
    if any(path.resolve().parent != directory or not path.is_file() for path in paths):
        raise ValueError(plan.files_error)
    identities = {Path(record["path"]).name: record for record in fingerprints(paths)}
    if identities != meta["artifacts"]:
        raise ValueError(plan.identity_error)
    summary = plan.summary()
    if any(meta.get(k) != v for k, v in summary.items()):
        raise ValueError(plan.summary_error)
    if (directory / "script.R").read_text(encoding="utf-8") != plan.script():
        raise ValueError(plan.script_error)
    return summary, identities
