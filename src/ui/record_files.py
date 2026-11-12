"""会话/项目记录的产物路径枚举（records CLI 与占用统计共用）。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from src.ui.session_store import SessionStore

_PROTECTED_NAMES = {".cellspatio", ".wrroc", "snapshots", "data"}


def session_runs(store: SessionStore, session_id: str) -> list[dict[str, Any]]:
    """返回会话已登记的 QC run 及其 attempts。"""
    runs = []
    for row in store.list_qc_runs(session_id):
        run = store.get_qc_run(session_id, row["run_id"])
        if run is not None:
            runs.append(run)
    return runs


def wrroc_runs(root: Path, session_id: str) -> list[tuple[str, dict[str, Any]]]:
    """枚举 .wrroc 下属于该会话的运行记录；坏文件跳过。"""
    return wrroc_index(root).get(session_id, [])


def wrroc_index(root: Path) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """一次扫描 .wrroc，按 session_id 归组运行记录；坏文件跳过。"""
    base = root / ".wrroc"
    if not base.is_dir():
        return {}
    index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for wf_file in sorted(base.glob("*/workflow.json")):
        try:
            data = json.loads(wf_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        context = ((data.get("input") or {}).get("context") or {}) or (
            (data.get("meta") or {}).get("context") or {}
        )
        session_id = context.get("session_id")
        if session_id:
            index.setdefault(str(session_id), []).append((wf_file.parent.name, data))
    return index


def run_dirs(run: dict[str, Any]) -> list[tuple[str, str]]:
    """返回 run 的 (stage, directory) 列表。"""
    pairs = []
    if run.get("directory"):
        pairs.append(("qc", str(run["directory"])))
    for key, stage in (
        ("attempts", "filter"),
        ("pca_attempts", "pca"),
        ("cluster_attempts", "cluster"),
        ("marker_attempts", "marker"),
    ):
        for attempt in run.get(key) or []:
            if attempt.get("directory"):
                pairs.append((stage, str(attempt["directory"])))
    return pairs


def resolve_dir(value: str, root: Path) -> Path:
    """把相对路径解析到 root 下。"""
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def writable_dir(path: Path) -> bool:
    """写探针校验目录可写（Windows ACL 下 os.access 不可靠）；探针文件立即删除。"""
    if not path.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".cellspatio-probe-", delete=True):
            return True
    except OSError:
        return False


def safe_target(value: str | Path, root: Path) -> Path | None:
    """只允许 root 内的普通产物路径；拒绝 root 本身、共享根与逃逸路径。"""
    path = resolve_dir(str(value), root)
    if path == root or any(path == root / name for name in _PROTECTED_NAMES):
        return None
    return path if path.is_relative_to(root) else None
