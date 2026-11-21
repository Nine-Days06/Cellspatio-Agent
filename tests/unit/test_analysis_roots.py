"""AnalysisRoots 的会话/项目/QC 路径接缝测试。"""

from pathlib import Path

import pytest

from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.db")


def test_for_session_uses_project_and_global_roots(tmp_path, monkeypatch):
    store = _store(tmp_path)
    project_id = store.create_project("项目")
    project_session = store.create_session(project_id=project_id)
    plain_session = store.create_session()

    monkeypatch.chdir(tmp_path)
    assert (
        AnalysisRoots(store, ".").for_session(project_session)
        == (tmp_path / "data" / "projects" / project_id / "analysis").resolve()
    )
    assert (
        AnalysisRoots(store, tmp_path).for_session(plain_session)
        == (tmp_path / "data" / "analysis").resolve()
    )
    assert (
        AnalysisRoots(store, tmp_path).for_session(None)
        == (tmp_path / "data" / "analysis").resolve()
    )


def test_for_session_rejects_unknown_session(tmp_path):
    roots = AnalysisRoots(_store(tmp_path), tmp_path)

    with pytest.raises(ValueError, match="^会话不存在，不能确定分析产物根$"):
        roots.for_session("nope")


def test_for_session_follows_store_factory_each_call(tmp_path):
    empty_store = _store(tmp_path / "empty")
    project_store = _store(tmp_path / "project")
    project_id = project_store.create_project("项目")
    project_session = project_store.create_session(project_id=project_id)
    current = {"store": empty_store}
    roots = AnalysisRoots(lambda: current["store"], tmp_path)

    with pytest.raises(ValueError, match="^会话不存在，不能确定分析产物根$"):
        roots.for_session(project_session)

    current["store"] = project_store
    assert (
        roots.for_session(project_session)
        == (tmp_path / "data" / "projects" / project_id / "analysis").resolve()
    )


def test_project_scoped_returns_only_project_roots(tmp_path, monkeypatch):
    store = _store(tmp_path)
    project_id = store.create_project("项目")
    project_session = store.create_session(project_id=project_id)
    plain_session = store.create_session()
    roots = AnalysisRoots(store, tmp_path)

    monkeypatch.chdir(tmp_path)
    assert (
        AnalysisRoots(store, ".").project_scoped(project_session)
        == (tmp_path / "data" / "projects" / project_id / "analysis").resolve()
    )
    assert (
        roots.project_scoped(project_session)
        == (tmp_path / "data" / "projects" / project_id / "analysis").resolve()
    )
    assert roots.project_scoped(plain_session) is None
    assert roots.project_scoped(None) is None


def test_project_scoped_rejects_unknown_session(tmp_path):
    roots = AnalysisRoots(_store(tmp_path), tmp_path)

    with pytest.raises(ValueError, match="^会话不存在，不能确定分析产物根$"):
        roots.project_scoped("nope")


def test_project_scoped_follows_store_factory_each_call(tmp_path):
    empty_store = _store(tmp_path / "empty")
    project_store = _store(tmp_path / "project")
    project_id = project_store.create_project("项目")
    project_session = project_store.create_session(project_id=project_id)
    current = {"store": empty_store}
    roots = AnalysisRoots(lambda: current["store"], tmp_path)

    with pytest.raises(ValueError, match="^会话不存在，不能确定分析产物根$"):
        roots.project_scoped(project_session)

    current["store"] = project_store
    assert (
        roots.project_scoped(project_session)
        == (tmp_path / "data" / "projects" / project_id / "analysis").resolve()
    )


def test_qc_root_accepts_legacy_and_project_roots(tmp_path):
    roots = AnalysisRoots(_store(tmp_path), tmp_path)
    legacy = tmp_path / "data" / "analysis" / "sc_qc"
    project = tmp_path / "data" / "projects" / "project-1" / "analysis" / "sc_qc"

    assert roots.qc_root({"directory": str(legacy / "run-1")}) == legacy.resolve()
    assert roots.qc_root({"directory": str(project / "run-2")}) == project.resolve()


def test_qc_root_uses_relative_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    roots = AnalysisRoots(_store(tmp_path), ".")

    assert (
        roots.qc_root({"directory": "data/analysis/sc_qc/run-1"})
        == (tmp_path / "data" / "analysis" / "sc_qc").resolve()
    )


def test_relative_repo_root_is_anchored_at_construction(tmp_path, monkeypatch):
    store = _store(tmp_path)
    project_id = store.create_project("项目")
    project_session = store.create_session(project_id=project_id)
    legacy = tmp_path / "data" / "analysis" / "sc_qc"

    monkeypatch.chdir(tmp_path)
    roots = AnalysisRoots(store, ".")
    monkeypatch.chdir(tmp_path.parent)

    assert (
        roots.for_session(project_session)
        == (tmp_path / "data" / "projects" / project_id / "analysis").resolve()
    )
    assert roots.qc_root({"directory": str(legacy / "run-1")}) == legacy.resolve()


def test_qc_root_rejects_directory_outside_allowed_roots(tmp_path):
    roots = AnalysisRoots(_store(tmp_path), tmp_path)
    outside = tmp_path.parent / "outside-qc" / "run-1"

    with pytest.raises(ValueError, match="^QC 目录非法，请重新 QC$"):
        roots.qc_root({"directory": str(outside)})


def test_qc_root_rejects_sc_qc_name_in_wrong_location(tmp_path):
    roots = AnalysisRoots(_store(tmp_path), tmp_path)
    misplaced = tmp_path / "elsewhere" / "sc_qc" / "run-1"

    with pytest.raises(ValueError, match="^QC 目录非法，请重新 QC$"):
        roots.qc_root({"directory": str(misplaced)})

    almost_project = tmp_path / "data" / "wrong" / "analysis" / "sc_qc" / "run-2"
    with pytest.raises(ValueError, match="^QC 目录非法，请重新 QC$"):
        roots.qc_root({"directory": str(almost_project)})
