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
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ui.record_files import (
    resolve_dir as _resolve_dir,
)
from src.ui.record_files import (
    run_dirs as _run_dirs,
)
from src.ui.record_files import (
    safe_target as _safe_target,
)
from src.ui.record_files import (
    session_runs as _session_runs,
)
from src.ui.record_files import (
    wrroc_runs as _wrroc_runs,
)
from src.ui.session_store import SessionStore

SCHEMA_VERSION = "cellspatio-record/1.0.0"
_PATH_RE = re.compile(r'(?:[A-Za-z]:[\\/][^\s"\'<>]*|/(?:Users|home|data|mnt|tmp)/[^\s"\'<>]*)')
_PATH_KEYS = {
    "access_path",
    "directory",
    "file_path",
    "kb_path",
    "path",
    "root_path",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_text(text: str) -> str:
    return _PATH_RE.sub("<redacted-path>", text)


def _json_safe(value: Any, redact: bool) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item, redact) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, redact) for item in value]
    if isinstance(value, str):
        return _redact_text(value) if redact else value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _redact_record_paths(value: Any) -> Any:
    if isinstance(value, dict):
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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_rows(
    runs: list[dict[str, Any]], session_id: str, root: Path, redact: bool
) -> list[dict[str, Any]]:
    rows = []
    seen: set[Path] = set()
    for stage, directory in ((stage, dir) for run in runs for stage, dir in _run_dirs(run)):
        path = _resolve_dir(directory, root)
        if path in seen or not path.is_dir():
            continue
        seen.add(path)
        for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
            record_path = (
                "<redacted-path>"
                if redact
                else str(
                    file_path.relative_to(root) if file_path.is_relative_to(root) else file_path
                )
            )
            rows.append(
                {
                    "session_id": session_id,
                    "stage": stage,
                    "path": record_path,
                    "bytes": file_path.stat().st_size,
                    "sha256": _sha256(file_path),
                }
            )
    return rows


def _session_record(
    store: SessionStore,
    session: dict[str, Any],
    root: Path,
    redact: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session = store.get_session(session["id"]) or session
    runs = _session_runs(store, session["id"])
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
    artifacts = _artifact_rows(runs, session["id"], root, redact)
    record = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": _now(),
        "session": session,
        "messages": messages,
        "qc_runs": runs,
        "workflow_runs": [data for _, data in _wrroc_runs(root, session["id"])],
        "artifacts": artifacts,
    }
    record = _json_safe(record, redact)
    if redact:
        record = _redact_record_paths(record)
    return record, artifacts


def _session_markdown(record: dict[str, Any]) -> str:
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
        lines.append(
            f"- `{run['run_id']}` · cells={run['evidence'].get('cells')} "
            f"features={run['evidence'].get('features')}"
        )
        for attempt in run.get("attempts") or []:
            lines.append(f"  - filter `{attempt['attempt_id']}` · {attempt['status']}")
        for attempt in run.get("pca_attempts") or []:
            lines.append(f"  - PCA `{attempt['attempt_id']}` · {attempt['status']}")
        for attempt in run.get("cluster_attempts") or []:
            lines.append(f"  - cluster `{attempt['attempt_id']}` · {attempt['status']}")
        for attempt in run.get("marker_attempts") or []:
            lines.append(f"  - marker `{attempt['attempt_id']}` · {attempt['status']}")
    lines.extend(["", "## 运行记录", ""])
    for run in record["workflow_runs"]:
        intent = run.get("intent") or {}
        lines.append(
            f"- `{run.get('run_id')}` · {intent.get('analysis_type') or intent.get('type')} · "
            f"{intent.get('original_input') or ''}"
        )
    lines.extend(["", "## 产物", ""])
    for artifact in record["artifacts"]:
        lines.append(f"- `{artifact['path']}` · {artifact['bytes']} bytes")
    return "\n".join(lines) + "\n"


def _artifacts_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["session_id", "stage", "path", "bytes", "sha256"])
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _write_zip(path: Path, files: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def export_records(
    scope: str,
    target_id: str,
    out: str | Path | None = None,
    *,
    redact: bool = False,
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
        record, artifacts = _session_record(store, session, base, redact)
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
            record, session_artifacts = _session_record(store, session, base, redact)
            files[f"sessions/{session['id']}/record.json"] = json.dumps(
                record, ensure_ascii=False, indent=2
            )
            files[f"sessions/{session['id']}/record.md"] = _session_markdown(record)
            artifacts.extend(session_artifacts)
        files["artifacts.csv"] = _artifacts_csv(artifacts)
    else:
        raise ValueError("scope 必须是 session 或 project")

    target = Path(out) if out else base / f"records-{target_id}.zip"
    target = target if target.is_absolute() else base / target
    _write_zip(target, files)
    return {
        "status": "ok",
        "scope": scope,
        "target_id": target_id,
        "out": str(target),
        "redacted": redact,
        "files": len(files),
        "artifacts": len(artifacts),
    }


def _delete_plan(
    scope: str, target_id: str, store: SessionStore, root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[Path], list[str]]:
    project = None
    if scope == "session":
        session = store.get_session(target_id)
        if session is None:
            raise ValueError("会话不存在")
        sessions = [session]
    elif scope == "project":
        project = store.get_project(target_id)
        if project is None:
            raise ValueError("项目不存在")
        sessions = [
            session
            for session in store.list_sessions(include_archived=True)
            if session["project_id"] == target_id
        ]
    else:
        raise ValueError("scope 必须是 session 或 project")

    paths: list[Path] = []
    skipped: list[str] = []
    for session in sessions:
        running = store.running_stage(session["id"])
        if running:
            raise ValueError(f"{running}运行中，不能删除会话 {session['id']}")
        for run in _session_runs(store, session["id"]):
            for _, directory in _run_dirs(run):
                path = _safe_target(directory, root)
                if path is None:
                    skipped.append(str(_resolve_dir(directory, root)))
                else:
                    paths.append(path)
            for name in (".wrroc", "snapshots"):
                candidate = _safe_target(root / name / str(run["run_id"]), root)
                if candidate is None:
                    skipped.append(str(root / name / str(run["run_id"])))
                else:
                    paths.append(candidate)
        for run_id, _ in _wrroc_runs(root, session["id"]):
            for name in (".wrroc", "snapshots"):
                candidate = _safe_target(root / name / run_id, root)
                if candidate is None:
                    skipped.append(str(root / name / run_id))
                else:
                    paths.append(candidate)
    if project is not None:
        for candidate in (
            root / "data" / "projects" / target_id,
            root / ".wrroc" / "kg_memory" / target_id,
        ):
            path = _safe_target(candidate, root)
            if path is None:
                skipped.append(str(candidate))
            else:
                paths.append(path)
    unique_paths = []
    seen: set[Path] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique_paths.append(path)
    return sessions, project, unique_paths, skipped


def delete_records(
    scope: str,
    target_id: str,
    *,
    dry_run: bool = False,
    cascade: bool = False,
    store: SessionStore | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """删除 DB 行与已知产物目录；dry_run 只列计划，不落盘。"""
    base = Path(root or Path.cwd()).resolve()
    if store is None:
        store = (
            SessionStore() if root is None else SessionStore(base / ".cellspatio" / "sessions.db")
        )
    sessions, project, paths, skipped = _delete_plan(scope, target_id, store, base)
    plan = {
        "scope": scope,
        "target_id": target_id,
        "sessions": [session["id"] for session in sessions],
        "paths": [str(path) for path in paths],
        "skipped": skipped,
        "cascade": cascade,
    }
    if dry_run:
        return {
            "status": "dry-run",
            **plan,
            "requires_cascade": bool(scope == "project" and sessions and not cascade),
        }

    if scope == "project" and sessions and not cascade:
        raise ValueError("项目仍有会话，需显式 --cascade")
    if skipped:
        raise ValueError("有产物在数据根之外，未删除任何记录: " + "; ".join(skipped))

    failures = []
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError as exc:
            failures.append({"path": str(path), "error": str(exc)})
    if failures:
        return {
            "status": "files-failed",
            **plan,
            "file_failures": failures,
            "verified": False,
        }

    try:
        for session in sessions:
            store.delete_session(session["id"])
        if project is not None:
            store.delete_project(target_id)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"status": "db-failed", "error": str(exc), **plan, "verified": False}

    remaining_sessions = [
        session["id"] for session in sessions if store.get_session(session["id"]) is not None
    ]
    remaining_paths = [str(path) for path in paths if path.exists()]
    verified = not remaining_sessions and not remaining_paths
    return {
        "status": "ok" if verified else "verify-failed",
        **plan,
        "deleted_sessions": len(sessions),
        "deleted_paths": len(paths) - len(remaining_paths),
        "remaining_sessions": remaining_sessions,
        "remaining_paths": remaining_paths,
        "verified": verified,
    }


def main(argv: list[str] | None = None) -> int:
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
