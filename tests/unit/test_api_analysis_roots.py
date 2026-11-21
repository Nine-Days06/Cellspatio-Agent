"""create_app 注入 AnalysisRoots，QC 路由只消费应用状态里的 roots。"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.analysis.qc_preview import register_evidence
from src.analysis.sc_qc import run_sc_qc
from src.api.app import create_app
from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore
from tests.unit.fakes import QC_HEADER, FakeAgent, fake_qc_executor

_API_DIR = Path(__file__).resolve().parents[2] / "src" / "api"


def _roots_attributes(tree: ast.Module) -> list[ast.Attribute]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "analysis_roots"
    ]


def _is_app_state_roots(node: ast.Attribute) -> bool:
    """`request.app.state.analysis_roots` 的 AST 形状。"""
    state, app, request = node.value, None, None
    if isinstance(state, ast.Attribute):
        app = state.value
    if isinstance(app, ast.Attribute):
        request = app.value
    return (
        isinstance(state, ast.Attribute)
        and state.attr == "state"
        and isinstance(app, ast.Attribute)
        and app.attr == "app"
        and isinstance(request, ast.Name)
        and request.id == "request"
    )


def _register_qc(
    store: SessionStore, tmp_path: Path, root: Path, project_id: str | None = None
) -> tuple[str, Path]:
    source = tmp_path / "counts.csv"
    if not source.exists():
        source.write_text("gene,c1,c2\nMT-X,1,1\nG1,0,3\n", encoding="utf-8")
    executor = fake_qc_executor(
        "feature_id,symbol,unique_name\nMT-X,MT-X,MT-X\nG1,G1,G1\n",
        QC_HEADER + b"c1,1,1,100,\nc2,4,2,25,\n",
    )
    result = run_sc_qc(str(source), executor, root)
    directory = Path(result["results"]["qc_artifacts"]["metadata.json"]).parent
    session_id = store.create_session(project_id=project_id)
    store.register_qc_run(
        session_id, directory.name, str(directory), register_evidence(directory, root)
    )
    return session_id, directory


def _client(recorder_repo: Path, allowed: Path) -> TestClient:
    agent = FakeAgent()
    agent.workflow_manager = SimpleNamespace(
        workflow_recorder=SimpleNamespace(repo_root=recorder_repo)
    )
    app = create_app(agent)
    app.state.analysis_roots = AnalysisRoots(SessionStore(), allowed)
    return TestClient(app)


def test_create_app_uses_workflow_recorder_repo_root(tmp_path):
    agent = FakeAgent()
    agent.workflow_manager = SimpleNamespace(workflow_recorder=SimpleNamespace(repo_root=tmp_path))

    roots = create_app(agent).state.analysis_roots

    assert isinstance(roots, AnalysisRoots)
    assert (
        roots.qc_root({"directory": str(tmp_path / "data/analysis/sc_qc/run-1")})
        == (tmp_path / "data" / "analysis" / "sc_qc").resolve()
    )


def test_create_app_falls_back_to_cwd_without_recorder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    roots = create_app(FakeAgent()).state.analysis_roots

    assert (
        roots.qc_root({"directory": "data/analysis/sc_qc/run-1"})
        == (tmp_path / "data" / "analysis" / "sc_qc").resolve()
    )


def test_create_app_roots_follow_replaced_store_factory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app(FakeAgent())
    store = SessionStore(tmp_path / "custom.db")
    project_id = store.create_project("项目")
    session_id = store.create_session(project_id=project_id)

    with pytest.raises(ValueError, match="会话不存在"):
        app.state.analysis_roots.for_session(session_id)

    app.state.store_factory = lambda: store
    assert (
        app.state.analysis_roots.for_session(session_id)
        == (tmp_path / "data" / "projects" / project_id / "analysis").resolve()
    )


def test_stage_routes_take_roots_from_app_state_not_agent():
    """PCA/cluster/marker 路由同样只读 app.state，不读 agent 上的 roots。"""
    offenders: list[str] = []
    for name in ("routes_pca.py", "routes_clusters.py", "routes_markers.py"):
        tree = ast.parse((_API_DIR / name).read_text(encoding="utf-8"), filename=name)
        roots = _roots_attributes(tree)
        assert roots, f"{name} 未使用 analysis_roots"
        offenders.extend(name for node in roots if not _is_app_state_roots(node))
    assert offenders == []


def test_qc_route_reads_injected_analysis_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    allowed = tmp_path / "allowed"
    root = allowed / "data" / "analysis" / "sc_qc"
    session_id, directory = _register_qc(SessionStore(), tmp_path, root)

    body = (
        _client(tmp_path / "wrong-repo", allowed)
        .get(f"/api/sessions/{session_id}/qc/{directory.name}")
        .json()
    )

    assert body["blocked"] == []


def test_qc_route_accepts_project_analysis_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    allowed = tmp_path / "allowed"
    store = SessionStore()
    project_id = store.create_project("项目")
    root = allowed / "data" / "projects" / project_id / "analysis" / "sc_qc"
    session_id, directory = _register_qc(store, tmp_path, root, project_id)

    body = (
        _client(tmp_path / "wrong-repo", allowed)
        .get(f"/api/sessions/{session_id}/qc/{directory.name}")
        .json()
    )

    assert body["blocked"] == []


def test_qc_route_rejects_directory_outside_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    allowed = tmp_path / "allowed"
    root = tmp_path / "outside" / "sc_qc"
    session_id, directory = _register_qc(SessionStore(), tmp_path, root)

    body = _client(allowed, allowed).get(f"/api/sessions/{session_id}/qc/{directory.name}").json()

    assert body["blocked"] == ["QC 目录非法，请重新 QC"]
