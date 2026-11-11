"""records CLI：导出 zip、脱敏与级联删除。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src.cli import records
from src.ui.session_store import SessionStore


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve()


@pytest.fixture
def store(root):
    return SessionStore(root / ".cellspatio" / "sessions.db")


def _seed_session(store, root, title="分析会话", run_id="run-1"):
    sid = store.create_session(title)
    run_dir = root / "data" / "analysis" / "qc" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "qc.csv").write_text("cell,n\n1,2\n", encoding="utf-8")
    store.register_qc_run(sid, run_id, str(run_dir), {"cells": 10, "features": 5})
    for rel in (
        f".wrroc/{run_id}/workflow.json",
        f"snapshots/{run_id}/snapshot.txt",
    ):
        path = root / rel
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")
    return sid, run_dir


def test_export_session_zip_contains_manifest(store, root):
    sid, _ = _seed_session(store, root)
    result = records.export_records("session", sid, out=root / "out.zip", store=store, root=root)

    assert result["status"] == "ok"
    with zipfile.ZipFile(root / "out.zip") as archive:
        names = set(archive.namelist())
        assert names == {"record.json", "record.md", "artifacts.csv"}
        record = json.loads(archive.read("record.json"))
        csv_text = archive.read("artifacts.csv").decode("utf-8")

    assert record["session"]["id"] == sid
    assert record["qc_runs"][0]["run_id"] == "run-1"
    assert str(Path("data") / "analysis" / "qc" / "run-1" / "qc.csv") in csv_text
    assert str(root) not in csv_text


def test_export_redacts_paths(store, root):
    sid, _ = _seed_session(store, root)
    store.append_message(sid, "user", f"读取 {root / 'data' / 'matrix.mtx'}")
    records.export_records(
        "session", sid, out=root / "redacted.zip", redact=True, store=store, root=root
    )

    with zipfile.ZipFile(root / "redacted.zip") as archive:
        record_text = archive.read("record.json").decode("utf-8")
        csv_text = archive.read("artifacts.csv").decode("utf-8")
        markdown = archive.read("record.md").decode("utf-8")

    assert str(root) not in record_text
    assert str(root) not in markdown
    assert "<redacted-path>" in record_text
    assert "<redacted-path>" in csv_text


def test_export_project_nests_sessions(store, root):
    pid = store.create_project("肝癌项目")
    sid, _ = _seed_session(store, root)
    store.move_session(sid, pid)

    records.export_records("project", pid, out=root / "project.zip", store=store, root=root)

    with zipfile.ZipFile(root / "project.zip") as archive:
        names = set(archive.namelist())
        project = json.loads(archive.read("project.json"))

    assert {"project.json", "project.md", "artifacts.csv"} <= names
    assert f"sessions/{sid}/record.json" in names
    assert f"sessions/{sid}/record.md" in names
    assert project["session_ids"] == [sid]


def test_export_and_delete_include_non_qc_wrroc(store, root):
    sid = store.create_session()
    wf_dir = root / ".wrroc" / "run-de"
    wf_dir.mkdir(parents=True)
    (wf_dir / "workflow.json").write_text(
        json.dumps(
            {
                "run_id": "run-de",
                "intent": {"type": "analysis", "analysis_type": "differential_expression"},
                "input": {"context": {"session_id": sid}},
            }
        ),
        encoding="utf-8",
    )

    records.export_records("session", sid, out=root / "out.zip", store=store, root=root)
    with zipfile.ZipFile(root / "out.zip") as archive:
        record = json.loads(archive.read("record.json"))
    assert record["workflow_runs"][0]["run_id"] == "run-de"

    assert records.delete_records("session", sid, store=store, root=root)["verified"] is True
    assert not wf_dir.exists()


def test_delete_dry_run_keeps_everything(store, root):
    sid, run_dir = _seed_session(store, root)
    result = records.delete_records("session", sid, dry_run=True, store=store, root=root)

    assert result["status"] == "dry-run"
    assert store.get_session(sid) is not None
    assert run_dir.is_dir()
    assert (root / ".wrroc" / "run-1").is_dir()


def test_delete_session_removes_rows_and_known_dirs(store, root):
    sid, run_dir = _seed_session(store, root)
    result = records.delete_records("session", sid, store=store, root=root)

    assert result["status"] == "ok"
    assert result["verified"] is True
    assert store.get_session(sid) is None
    assert not run_dir.exists()
    assert not (root / ".wrroc" / "run-1").exists()
    assert not (root / "snapshots" / "run-1").exists()


def test_delete_project_requires_cascade(store, root):
    pid = store.create_project("非空项目")
    sid, _ = _seed_session(store, root)
    store.move_session(sid, pid)

    with pytest.raises(ValueError, match="cascade"):
        records.delete_records("project", pid, store=store, root=root)
    assert store.get_session(sid) is not None
    assert store.get_project(pid) is not None


def test_delete_project_cascade_removes_all_sessions(store, root):
    pid = store.create_project("肝癌项目")
    first, _ = _seed_session(store, root, "会话一", "run-1")
    second, _ = _seed_session(store, root, "会话二", "run-2")
    store.move_session(first, pid)
    store.move_session(second, pid)
    (root / "data" / "projects" / pid).mkdir(parents=True)
    (root / ".wrroc" / "kg_memory" / pid).mkdir(parents=True)

    result = records.delete_records("project", pid, cascade=True, store=store, root=root)

    assert result["verified"] is True
    assert store.get_project(pid) is None
    assert store.list_sessions(include_archived=True) == []
    assert not (root / "data" / "projects" / pid).exists()
    assert not (root / ".wrroc" / "kg_memory" / pid).exists()


def test_delete_rejects_paths_outside_root(store, root):
    sid = store.create_session()
    outside = root.parent / "outside"
    outside.mkdir()
    store.register_qc_run(sid, "run-x", str(outside), {"summary": {"cells": 1, "features": 1}})

    with pytest.raises(ValueError, match="数据根之外"):
        records.delete_records("session", sid, store=store, root=root)
    assert store.get_session(sid) is not None
    assert outside.is_dir()


def test_delete_file_failure_keeps_db(store, root, monkeypatch):
    sid, _ = _seed_session(store, root)

    def fail(path):
        raise OSError("locked")

    monkeypatch.setattr(records.shutil, "rmtree", fail)
    result = records.delete_records("session", sid, store=store, root=root)

    assert result["status"] == "files-failed"
    assert result["verified"] is False
    assert store.get_session(sid) is not None


def test_project_dry_run_reports_requires_cascade(store, root):
    pid = store.create_project("非空项目")
    sid, _ = _seed_session(store, root)
    store.move_session(sid, pid)

    result = records.delete_records("project", pid, dry_run=True, store=store, root=root)

    assert result["status"] == "dry-run"
    assert result["requires_cascade"] is True


def test_main_delete_requires_yes(store, root, monkeypatch):
    sid, _ = _seed_session(store, root)
    monkeypatch.chdir(root)

    assert records.main(["delete", "session", sid, "--root", str(root)]) == 2
    assert store.get_session(sid) is not None
    assert records.main(["delete", "session", sid, "--root", str(root), "--yes"]) == 0
    assert store.get_session(sid) is None


def test_main_root_reads_target_database(root, monkeypatch):
    sid, _ = _seed_session(SessionStore(root / ".cellspatio" / "sessions.db"), root)
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert records.main(["delete", "session", sid, "--root", str(root), "--dry-run"]) == 0
