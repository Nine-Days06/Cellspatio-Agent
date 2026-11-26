"""工作区产物根、到期清理与 2 GB 配额。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore
from src.ui.workspace_cleanup import cleanup_expired_workspaces, workspace_usage_bytes


def _workspace(store: SessionStore, root, kind: str = "ephemeral") -> str:
    wid = store.create_workspace(kind=kind, label="测试工作区")
    store.set_workspace_root(wid, str(root / "data" / "workspaces" / wid))
    return wid


def test_workspace_artifacts_live_under_workspace_root(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    wid = _workspace(store, tmp_path)
    scoped = SessionStore(store.db_path, workspace_id=wid)
    project = scoped.create_project("P")
    sid = scoped.create_session("S", project_id=project)

    root = AnalysisRoots(scoped, tmp_path).for_session(sid)

    assert root == (tmp_path / "data" / "workspaces" / wid / "projects" / project / "analysis")


def test_legacy_session_keeps_old_root(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session("legacy")

    assert AnalysisRoots(store, tmp_path).for_session(sid) == tmp_path / "data" / "analysis"


def test_cleanup_removes_expired_workspace_and_files(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    wid = _workspace(store, tmp_path)
    root = tmp_path / "data" / "workspaces" / wid
    root.mkdir(parents=True)
    (root / "result.bin").write_bytes(b"x" * 10)
    sid = SessionStore(store.db_path, workspace_id=wid).create_session("old")
    run_dir = tmp_path / ".wrroc" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "workflow.json").write_text(
        json.dumps({"input": {"context": {"session_id": sid}}}), encoding="utf-8"
    )
    store.set_workspace_status(wid, "active")
    with store._conn() as conn:
        conn.execute(
            "UPDATE workspaces SET expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), wid),
        )

    result = cleanup_expired_workspaces(store, tmp_path)

    assert result["count"] == 1
    assert store.get_workspace(wid) is None
    assert not root.exists()
    assert not run_dir.exists()


def test_cleanup_skips_workspace_with_running_stage(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions.db")
    wid = _workspace(store, tmp_path)
    SessionStore(store.db_path, workspace_id=wid).create_session("running")
    with store._conn() as conn:
        conn.execute(
            "UPDATE workspaces SET expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), wid),
        )
    monkeypatch.setattr(SessionStore, "running_stage", lambda self, sid: "qc_review")

    result = cleanup_expired_workspaces(store, tmp_path)

    assert result["count"] == 0
    assert result["skipped"] == [{"workspace_id": wid, "reason": "分析运行中"}]
    assert store.get_workspace(wid) is not None


def test_workspace_usage_bytes_counts_files(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    wid = _workspace(store, tmp_path)
    root = tmp_path / "data" / "workspaces" / wid
    root.mkdir(parents=True)
    (root / "a").write_bytes(b"12345")
    (root / "b").write_bytes(b"123")

    assert workspace_usage_bytes(store, wid) == 8
