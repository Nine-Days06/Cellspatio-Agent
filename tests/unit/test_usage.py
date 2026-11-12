"""记录占用只读汇总：产物 + .wrroc + snapshots，按需计算不清理。"""

from __future__ import annotations

import json

import pytest

from src.ui.session_store import SessionStore
from src.ui.usage import project_usage, session_usage


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve()


@pytest.fixture
def store(root):
    return SessionStore(root / ".cellspatio" / "sessions.db")


def _seed(store, root, run_id="run-1"):
    sid = store.create_session("分析会话")
    run_dir = root / "data" / "analysis" / "sc_qc" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "qc.csv").write_text("12345", encoding="utf-8")
    store.register_qc_run(sid, run_id, str(run_dir), {"cells": 1, "features": 1})
    (root / ".wrroc" / run_id / "workflow.json").parent.mkdir(parents=True)
    (root / ".wrroc" / run_id / "workflow.json").write_text("{}", encoding="utf-8")
    (root / "snapshots" / run_id / "s.txt").parent.mkdir(parents=True)
    (root / "snapshots" / run_id / "s.txt").write_text("abcdefg", encoding="utf-8")
    return sid, run_dir


def test_session_usage_sums_artifacts_wrroc_and_snapshots(store, root):
    sid, run_dir = _seed(store, root)

    usage = session_usage(store, sid, root)

    assert usage["scope"] == "session"
    assert usage["bytes"] == (
        (run_dir / "qc.csv").stat().st_size
        + (root / ".wrroc" / "run-1" / "workflow.json").stat().st_size
        + 7
    )
    assert usage["files"] == 3


def test_project_usage_adds_project_dirs_without_double_counting(store, root):
    pid = store.create_project("肝癌项目")
    sid, _ = _seed(store, root)
    store.move_session(sid, pid)
    project_run = root / "data" / "projects" / pid / "analysis" / "sc_qc" / "run-2"
    project_run.mkdir(parents=True)
    (project_run / "qc.csv").write_text("123", encoding="utf-8")
    store.register_qc_run(sid, "run-2", str(project_run), {"cells": 2, "features": 1})
    unregistered = root / "data" / "projects" / pid / "analysis" / "de" / "x.csv"
    unregistered.parent.mkdir(parents=True)
    unregistered.write_text("1234", encoding="utf-8")
    kg_file = root / ".wrroc" / "kg_memory" / pid / "kg.json"
    kg_file.parent.mkdir(parents=True)
    kg_file.write_text("12", encoding="utf-8")

    session = session_usage(store, sid, root)
    usage = project_usage(store, pid, root)

    # 项目目录整树算一次（含未登记 de），其下的 run-2 不再重复累加
    assert usage["scope"] == "project"
    assert usage["bytes"] == 23
    assert usage["files"] == 6
    assert session["bytes"] == 17


def test_usage_rejects_unknown_targets(store, root):
    with pytest.raises(ValueError, match="会话不存在"):
        session_usage(store, "nope", root)
    with pytest.raises(ValueError, match="项目不存在"):
        project_usage(store, "nope", root)


def test_session_usage_finds_wrroc_runs_by_context_and_skips_bad_json(store, root):
    sid = store.create_session("会话")
    wf = root / ".wrroc" / "run-9" / "workflow.json"
    wf.parent.mkdir(parents=True)
    wf.write_text(json.dumps({"input": {"context": {"session_id": sid}}}), encoding="utf-8")
    bad = root / ".wrroc" / "run-bad" / "workflow.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{oops", encoding="utf-8")

    usage = session_usage(store, sid, root)

    assert usage["bytes"] == wf.stat().st_size
    assert usage["files"] == 1
