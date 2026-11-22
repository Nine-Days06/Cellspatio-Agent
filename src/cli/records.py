"""CLI: 导出/删除项目或会话记录。

python -m src.cli.records export session <session_id> [--out <zip>] [--redact]
python -m src.cli.records export project <project_id> [--out <zip>] [--redact]
python -m src.cli.records delete session <session_id> [--dry-run] [--yes]
python -m src.cli.records delete project <project_id> [--dry-run] [--cascade] [--yes]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ui.record_delete import delete_records
from src.ui.record_files import (
    resolve_dir,
    run_dirs,
    safe_target,
    session_runs,
    writable_dir,
    wrroc_runs,
)
from src.ui.run_snapshot import RunSnapshot
from src.ui.session_store import SessionStore

SCHEMA_VERSION = "cellspatio-record/1.2.0"
# 路径可能含空格；宁可多吞同行文本，也不能只脱敏空格前的目录而泄露用户名/文件名。
# URL 边界只限制 ASCII 词字符；中文紧邻的绝对路径仍须脱敏。
_PATH_RE = re.compile(
    r'(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\n\r"\'<>]*'
    r'|\\{2,}[^\n\r"\'<>]*|(?<![A-Za-z0-9_:/])/[^\n\r"\'<>]*)'
)
_PATH_KEYS = {
    "access_path",
    "directory",
    "file_path",
    "kb_path",
    "path",
    "root_path",
    "input_file",
    "output_file",
    "plot_file",
    "capsule_dir",
}


def _now() -> str:
    """当前 UTC 时间（ISO 8601）。"""
    return datetime.now(timezone.utc).isoformat()


def _redact_text(text: str) -> str:
    """把文本中的绝对路径替换为占位符。"""
    return _PATH_RE.sub("<redacted-path>", text)


def _json_safe(value: Any, redact: bool) -> Any:
    """递归脱敏字符串并保证值可 JSON 序列化。"""
    if isinstance(value, Mapping):
        return {key: _json_safe(item, redact) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, redact) for item in value]
    if isinstance(value, str):
        return _redact_text(value) if redact else value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _redact_record_paths(value: Any) -> Any:
    """按已知路径字段名递归脱敏记录中的路径。"""
    if isinstance(value, Mapping):
        return {
            key: (
                "<redacted-path>"
                if key in _PATH_KEYS and isinstance(item, str)
                else _redact_record_paths(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_record_paths(item) for item in value]
    return value


def _strip_charts(results: Any) -> Any:
    """导出时只保留图表类型，避免把大体积绘图数据写进记录。"""
    if not isinstance(results, dict):
        return results
    safe = dict(results)
    charts = safe.get("charts")
    if isinstance(charts, list):
        safe["charts"] = [
            {"type": item.get("type") if isinstance(item, dict) else "unknown"} for item in charts
        ]
    return safe


def _sha256(path: Path) -> str:
    """流式计算文件 SHA256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_row(
    file_path: Path,
    session_id: str,
    stage: str,
    root: Path,
    redact: bool,
    hash_files: bool,
    run_id: str = "",
    source: str = "stage_directory",
) -> dict[str, Any]:
    """构造一行产物清单；redact 时路径脱敏。"""
    record_path = (
        "<redacted-path>"
        if redact
        else str(file_path.relative_to(root) if file_path.is_relative_to(root) else file_path)
    )
    return {
        "session_id": session_id,
        "run_id": run_id,
        "source": source,
        "stage": stage,
        "path": record_path,
        "bytes": file_path.stat().st_size,
        "sha256": _sha256(file_path) if hash_files else "",
    }


def _artifact_files(value: str, root: Path) -> list[Path]:
    """展开 root 内的普通文件/目录；拒绝路径及祖先中的符号链接。"""
    raw = Path(value)
    raw = raw if raw.is_absolute() else root / raw
    if raw.is_symlink() or any(parent.is_symlink() for parent in raw.parents):
        return []
    path = safe_target(raw, root)
    if path is None:
        return []
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        item
        for item in path.rglob("*")
        if item.is_file()
        and not item.is_symlink()
        and not any(parent.is_symlink() for parent in item.parents)
        and item.resolve().is_relative_to(root)
    )


def _workflow_output_rows(
    workflows: list[tuple[str, dict[str, Any]]],
    session_id: str,
    root: Path,
    redact: bool,
    hash_files: bool,
) -> list[dict[str, Any]]:
    """只按已登记的 WRROC outputs 关联运行；目录邻近不能证明身份。"""
    rows = []
    seen: set[tuple[str, Path]] = set()
    for run_id, run in workflows:
        if run.get("run_id") != run_id:
            continue
        stage = (run.get("intent") or {}).get("analysis_type") or "workflow"
        for output in run.get("outputs") or []:
            if not isinstance(output, Mapping) or not isinstance(output.get("path"), str):
                continue
            for file_path in _artifact_files(output["path"], root):
                identity = (run_id, file_path)
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(
                    _file_row(
                        file_path,
                        session_id,
                        stage,
                        root,
                        redact,
                        hash_files,
                        run_id,
                        "wrroc_output",
                    )
                )
    return rows


def _artifact_rows(
    runs: Iterable[Mapping[str, Any]],
    session_id: str,
    root: Path,
    redact: bool,
    hash_files: bool,
) -> list[dict[str, Any]]:
    """枚举已登记 run 目录下的全部文件。"""
    rows = []
    seen: set[Path] = set()
    for run in runs:
        for stage, directory in run_dirs(run):
            for file_path in _artifact_files(directory, root):
                if file_path in seen:
                    continue
                seen.add(file_path)
                rows.append(
                    _file_row(file_path, session_id, stage, root, redact, hash_files, run["run_id"])
                )
    return rows


def _project_extra_rows(
    store: SessionStore,
    sessions: list[dict[str, Any]],
    project_id: str,
    root: Path,
    redact: bool,
    hash_files: bool,
) -> list[dict[str, Any]]:
    """补录项目目录下未登记 run 的产物（如 de/spatial）。"""
    project_dir = safe_target(root / "data" / "projects" / project_id, root)
    if project_dir is None or not project_dir.is_dir():
        return []
    covered = [
        resolve_dir(directory, root)
        for session in sessions
        for run in session_runs(store, session["id"])
        for _, directory in run_dirs(run)
    ]
    registered_files = {
        root / row["path"]
        for session in sessions
        for row in _workflow_output_rows(
            wrroc_runs(root, session["id"]), session["id"], root, False, False
        )
    }
    rows = []
    for file_path in _artifact_files(str(project_dir), root):
        if file_path in registered_files or any(file_path.is_relative_to(path) for path in covered):
            continue
        parts = file_path.relative_to(project_dir).parts
        if len(parts) > 2 and parts[0] == "analysis":
            stage = parts[1]
        else:
            stage = parts[0] if len(parts) > 1 else "project"
        rows.append(
            _file_row(file_path, "", stage, root, redact, hash_files, source="directory_discovery")
        )
    return rows


def _session_record(
    store: SessionStore,
    session: dict[str, Any],
    root: Path,
    redact: bool,
    hash_files: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """装配单个会话的导出记录与产物清单。"""
    session = store.get_session(session["id"]) or session
    runs = session_runs(store, session["id"])
    messages = []
    for message in store.get_messages(session["id"]):
        messages.append(
            {
                "id": message["id"],
                "seq": message["seq"],
                "role": message["role"],
                "content": message["content"],
                "timestamp": message["timestamp"],
                "results": _strip_charts(message["results"]),
                "pending_script": message["pending_script"],
            }
        )
    workflows = wrroc_runs(root, session["id"])
    workflow_artifacts = _workflow_output_rows(workflows, session["id"], root, False, hash_files)
    # WRROC 的显式关联优先；同一固定阶段文件不能在清单里再次登记。
    workflow_paths = {row["path"] for row in workflow_artifacts}
    artifacts = [
        row
        for row in _artifact_rows(runs, session["id"], root, False, hash_files)
        if row["path"] not in workflow_paths
    ] + workflow_artifacts
    if redact:
        for row in artifacts:
            row["path"] = "<redacted-path>"
    record = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": _now(),
        "session": session,
        "messages": messages,
        "qc_runs": runs,
        "workflow_runs": [data for _, data in workflows],
        "artifacts": artifacts,
    }
    record = _json_safe(record, redact)
    if redact:
        record = _redact_record_paths(record)
    return record, artifacts


def _session_markdown(record: dict[str, Any]) -> str:
    """把会话记录渲染成可脱机阅读的 Markdown。"""
    session = record["session"]
    lines = [
        f"# 会话 {session['id']} — {session['title']}",
        "",
        f"- 项目：{session.get('project_id') or '未归类'}",
        f"- 创建：{session['created_at']}",
        f"- 更新：{session['updated_at']}",
        f"- 置顶：{'是' if session.get('pinned') else '否'}",
        "",
        "## 消息",
        "",
    ]
    for message in record["messages"]:
        lines.append(f"### [{message['timestamp']}] {message['role']}")
        lines.append("")
        lines.append(message["content"])
        lines.append("")
    lines.extend(["## QC runs", ""])
    for run in record["qc_runs"]:
        snapshot = RunSnapshot.coerce(run)
        lines.append(
            f"- `{run['run_id']}` · cells={run['evidence'].get('cells')} "
            f"features={run['evidence'].get('features')}"
        )
        for stage, label in (
            ("filter", "filter"),
            ("pca", "PCA"),
            ("cluster", "cluster"),
            ("marker", "marker"),
        ):
            for attempt in snapshot.stage_attempts(stage):
                lines.append(f"  - {label} `{attempt['attempt_id']}` · {attempt['status']}")
    lines.extend(["", "## 运行记录", ""])
    for run in record["workflow_runs"]:
        intent = run.get("intent") or {}
        lines.append(
            f"- `{run.get('run_id')}` · {intent.get('analysis_type') or intent.get('type')} · "
            f"{intent.get('original_input') or ''}"
        )
    lines.extend(["", "## 产物", ""])
    for artifact in record["artifacts"]:
        lines.append(
            f"- `{artifact['path']}` · {artifact['bytes']} bytes · "
            f"run={artifact['run_id'] or '未关联'} · {artifact['source']}"
        )
    return "\n".join(lines) + "\n"


def _artifacts_csv(rows: list[dict[str, Any]]) -> str:
    """产物清单 CSV。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=["session_id", "run_id", "source", "stage", "path", "bytes", "sha256"]
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write_zip(path: Path, files: dict[str, str]) -> None:
    """把内存中的文本文件写成 zip。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _default_export_dir(store: SessionStore, scope: str, target_id: str, base: Path) -> Path:
    """项目工作目录可用时作为导出默认目录，否则回退数据根。"""
    if scope == "project":
        project = store.get_project(target_id)
    else:
        session = store.get_session(target_id)
        project = (
            store.get_project(session["project_id"]) if session and session["project_id"] else None
        )
    root_path = (project or {}).get("root_path")
    if root_path:
        path = Path(root_path)
        if writable_dir(path):
            return path
    return base


def export_records(
    scope: str,
    target_id: str,
    out: str | Path | None = None,
    *,
    redact: bool = False,
    hash_files: bool = False,
    store: SessionStore | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """导出 session 或 project 为 zip；不复制产物内容，只写清单。"""
    base = Path(root or Path.cwd()).resolve()
    if store is None:
        store = (
            SessionStore() if root is None else SessionStore(base / ".cellspatio" / "sessions.db")
        )
    if scope == "session":
        session = store.get_session(target_id)
        if session is None:
            raise ValueError("会话不存在")
        record, artifacts = _session_record(store, session, base, redact, hash_files)
        files = {
            "record.json": json.dumps(record, ensure_ascii=False, indent=2),
            "record.md": _session_markdown(record),
            "artifacts.csv": _artifacts_csv(artifacts),
        }
    elif scope == "project":
        project = store.get_project(target_id)
        if project is None:
            raise ValueError("项目不存在")
        sessions = [
            session
            for session in store.list_sessions(include_archived=True)
            if session["project_id"] == target_id
        ]
        files = {
            "project.json": json.dumps(
                _json_safe(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "exported_at": _now(),
                        "project": project,
                        "session_ids": [s["id"] for s in sessions],
                    },
                    redact,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            "project.md": "\n".join(
                [f"# 项目 {project['label']}", ""]
                + [f"- `{s['id']}` — {s['title']}" for s in sessions]
            )
            + "\n",
        }
        artifacts = []
        for session in sessions:
            record, session_artifacts = _session_record(store, session, base, redact, hash_files)
            files[f"sessions/{session['id']}/record.json"] = json.dumps(
                record, ensure_ascii=False, indent=2
            )
            files[f"sessions/{session['id']}/record.md"] = _session_markdown(record)
            artifacts.extend(session_artifacts)
        artifacts.extend(_project_extra_rows(store, sessions, target_id, base, redact, hash_files))
        files["artifacts.csv"] = _artifacts_csv(artifacts)
    else:
        raise ValueError("scope 必须是 session 或 project")

    if out:
        target = Path(out)
    else:
        target = _default_export_dir(store, scope, target_id, base) / (f"records-{target_id}.zip")
    target = target if target.is_absolute() else base / target
    _write_zip(target, files)
    return {
        "status": "ok",
        "scope": scope,
        "target_id": target_id,
        "out": str(target),
        "redacted": redact,
        "hashed": hash_files,
        "files": len(files),
        "artifacts": len(artifacts),
    }


def main(argv: list[str] | None = None) -> int:
    """解析参数并执行 export/delete；返回进程退出码。"""
    parser = argparse.ArgumentParser(description="导出或删除 CellSpatio 项目/会话记录")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("export", "delete"):
        command = subparsers.add_parser(name)
        command.add_argument("scope", choices=("session", "project"))
        command.add_argument("target_id")
        command.add_argument("--root", default=None, help="数据目录（默认 cwd）")
        if name == "export":
            command.add_argument("--out", default=None)
            command.add_argument("--redact", action="store_true")
            command.add_argument(
                "--hash", action="store_true", help="逐文件计算 SHA256（默认只记路径与大小）"
            )
        else:
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--cascade", action="store_true")
            command.add_argument("--yes", action="store_true", help="确认不可恢复的硬删除")

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_records(
                args.scope,
                args.target_id,
                out=args.out,
                redact=args.redact,
                hash_files=args.hash,
                root=args.root,
            )
        else:
            if not args.dry_run and not args.yes:
                raise ValueError("实际删除需显式 --yes；请先运行 --dry-run 查看清单")
            result = delete_records(
                args.scope,
                args.target_id,
                dry_run=args.dry_run,
                cascade=args.cascade,
                root=args.root,
            )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("ok", "dry-run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
