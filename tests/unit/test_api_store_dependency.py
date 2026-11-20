"""app.state.store_factory 可替换：路由与启动中断恢复都经 factory 取 store。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent

_API_DIR = Path(__file__).resolve().parents[2] / "src" / "api"


def test_api_modules_do_not_construct_session_store_directly() -> None:
    """HTTP 层只能经 get_store 取 store，直接构造会绕开 app.state.store_factory。"""
    offenders = [
        str(path.relative_to(_API_DIR.parents[1]))
        for path in _API_DIR.rglob("*.py")
        if "SessionStore(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


class _RecordingStages:
    """最小 stages 替身：只记录启动恢复是否触达。"""

    def __init__(self, owner: RecordingStore):
        self._owner = owner

    def interrupt_all(self) -> None:
        self._owner.interrupted = True


class RecordingStore(SessionStore):
    """记录实例与调用次数的真实存储子类，数据库行为不变。"""

    instances: list[RecordingStore] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interrupted = False
        self.calls: list[str] = []
        RecordingStore.instances.append(self)

    @property
    def stages(self) -> _RecordingStages:
        return _RecordingStages(self)

    def list_sessions(self, include_archived: bool = False) -> list[dict]:
        self.calls.append("list_sessions")
        return super().list_sessions(include_archived=include_archived)

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        self.calls.append("list_projects")
        return super().list_projects(include_archived=include_archived)

    def get_session(self, session_id: str) -> dict | None:
        self.calls.append("get_session")
        return super().get_session(session_id)

    def get_qc_run(self, session_id: str, run_id: str) -> dict | None:
        self.calls.append("get_qc_run")
        return super().get_qc_run(session_id, run_id)


def test_lifespan_interrupt_recovery_uses_app_factory():
    app = create_app(FakeAgent())
    RecordingStore.instances.clear()
    app.state.store_factory = RecordingStore

    with TestClient(app):
        pass

    assert RecordingStore.instances
    assert RecordingStore.instances[0].interrupted is True


def test_sessions_projects_sidebar_use_overridden_factory_once_per_request():
    app = create_app(FakeAgent())
    RecordingStore.instances.clear()
    app.state.store_factory = RecordingStore

    with TestClient(app) as client:
        baseline = len(RecordingStore.instances)

        assert client.get("/api/sessions").status_code == 200
        assert len(RecordingStore.instances) == baseline + 1
        assert RecordingStore.instances[-1].calls == ["list_sessions"]

        assert client.get("/api/projects").status_code == 200
        assert len(RecordingStore.instances) == baseline + 2
        assert RecordingStore.instances[-1].calls == ["list_projects"]

        assert client.get("/api/sidebar", params={"session_id": "missing"}).status_code == 200
        assert len(RecordingStore.instances) == baseline + 3
        assert RecordingStore.instances[-1].calls == ["get_session"]


@pytest.mark.parametrize(
    ("method", "url", "payload", "expected_call"),
    [
        ("POST", "/api/chat", {"session_id": "missing", "prompt": "hi"}, "get_session"),
        ("POST", "/api/sessions/missing/local-input", {"path": "missing"}, "get_session"),
        ("GET", "/api/sessions/missing/qc/missing", None, "get_qc_run"),
        (
            "POST",
            "/api/sessions/missing/qc/missing/preview",
            {"n_feature_min": 1, "n_feature_max": 10, "percent_mt_max": 5},
            "get_qc_run",
        ),
        (
            "POST",
            "/api/sessions/missing/qc/missing/approve",
            {"preview_id": "p", "reason": "r"},
            "get_qc_run",
        ),
        (
            "POST",
            "/api/sessions/missing/qc/missing/reject",
            {"preview_id": "p", "reason": "r"},
            "get_qc_run",
        ),
        (
            "POST",
            "/api/sessions/missing/qc/missing/filter",
            {"preview_id": "p"},
            "get_qc_run",
        ),
    ],
)
def test_chat_input_qc_routes_use_overridden_factory(
    method: str, url: str, payload: dict | None, expected_call: str
):
    app = create_app(FakeAgent())
    RecordingStore.instances.clear()
    app.state.store_factory = RecordingStore

    with TestClient(app) as client:
        baseline = len(RecordingStore.instances)

        assert client.request(method, url, json=payload).status_code == 404
        assert len(RecordingStore.instances) == baseline + 1
        assert RecordingStore.instances[-1].calls == [expected_call]


@pytest.mark.parametrize(
    ("method", "url", "payload"),
    [
        ("GET", "/api/sessions/missing/qc/missing/pca/f", None),
        (
            "POST",
            "/api/sessions/missing/qc/missing/pca/f",
            {"binding_sha256": "0" * 64, "reason": "r"},
        ),
        (
            "GET",
            "/api/sessions/missing/qc/missing/pca/f/a/dimensions",
            None,
        ),
        ("GET", "/api/sessions/missing/qc/missing/clusters/f/p", None),
        (
            "POST",
            "/api/sessions/missing/qc/missing/clusters/f/p/execute",
            {
                "binding_sha256": "0" * 64,
                "reason": "r",
                "revision": 0,
                "dimension_review_id": "0" * 32,
            },
        ),
        ("GET", "/api/sessions/missing/qc/missing/markers/f/p/c", None),
        (
            "POST",
            "/api/sessions/missing/qc/missing/markers/f/p/c/execute",
            {
                "binding_sha256": "0" * 64,
                "reason": "r",
                "revision": 0,
                "cluster_review_id": "0" * 32,
            },
        ),
    ],
)
def test_pca_cluster_marker_routes_use_overridden_factory(
    method: str, url: str, payload: dict | None
):
    """缺失 session/run 的读写端点只经工厂构造一次 store 并返回 404。"""
    app = create_app(FakeAgent())
    RecordingStore.instances.clear()
    app.state.store_factory = RecordingStore

    with TestClient(app) as client:
        baseline = len(RecordingStore.instances)

        assert client.request(method, url, json=payload).status_code == 404
        assert len(RecordingStore.instances) == baseline + 1
        assert RecordingStore.instances[-1].calls == ["get_qc_run"]
