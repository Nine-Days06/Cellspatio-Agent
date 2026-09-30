"""GET /api/sidebar 形状与降级路径；python -m src.api 启动参数。"""
from __future__ import annotations

import importlib
import threading

from fastapi.testclient import TestClient

from src.api.app import create_app
from tests.unit.fakes import FakeAgent


class StubKB:
    """最小 knowledge_client 替身：可返回统计，也可抛错。"""

    def __init__(self, stats=None, error=None):
        self._stats = stats or {"working_dir": "./knowledge_base", "initialized": True}
        self._error = error

    def get_statistics(self):
        if self._error:
            raise self._error
        return self._stats


class StubExecutor:
    """最小 r_executor 替身：只提供 _resolve_rscript，可返回路径、空值或抛错。"""

    def __init__(self, rscript: str | None, error: Exception | None = None):
        self._rscript = rscript
        self._error = error

    def _resolve_rscript(self) -> str:
        if self._error:
            raise self._error
        return self._rscript


def _client(agent: FakeAgent) -> TestClient:
    return TestClient(create_app(agent))


def _rscript_file(tmp_path):
    """真实落一个 Rscript 可执行文件占位：_env_status 会做 Path.exists() 判定。"""
    path = tmp_path / "Rscript"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def test_sidebar_returns_kb_env_and_soft_warn(tmp_path):
    agent = FakeAgent()
    agent.knowledge_client = StubKB()
    agent.r_executor = StubExecutor(str(_rscript_file(tmp_path)))
    agent.config = {"knowledge_dir": str(tmp_path)}

    body = _client(agent).get("/api/sidebar").json()

    assert set(body) == {"kb_stats", "env", "soft_warn"}
    assert body["kb_stats"]["initialized"] is True
    assert body["env"]["rscript"] == str(tmp_path / "Rscript")
    assert body["env"]["kb_path"] == str(tmp_path)
    assert body["env"]["kb_ok"] is True
    assert body["soft_warn"] is False


def test_sidebar_degrades_when_knowledge_client_raises(tmp_path):
    agent = FakeAgent()
    agent.knowledge_client = StubKB(error=RuntimeError("kb down"))
    agent.r_executor = StubExecutor(None)
    # 指向 tmp_path 下不存在的目录：不依赖仓库里是否已有 knowledge_base/
    missing = tmp_path / "absent-kb"
    agent.config = {"knowledge_dir": str(missing)}

    response = _client(agent).get("/api/sidebar")

    assert response.status_code == 200
    body = response.json()
    assert body["kb_stats"] == {"error": "kb down", "initialized": False}
    assert body["env"]["kb_path"] == str(missing)
    assert body["env"]["kb_ok"] is False


def test_sidebar_degrades_when_rscript_probe_raises(monkeypatch):
    agent = FakeAgent()
    agent.knowledge_client = StubKB()
    agent.r_executor = StubExecutor(None, error=RuntimeError("no R"))
    agent.config = {}
    monkeypatch.setattr("shutil.which", lambda name: None)

    body = _client(agent).get("/api/sidebar").json()

    assert body["env"]["rscript"] is None


def test_sidebar_falls_back_to_which_rscript(monkeypatch):
    agent = FakeAgent()
    agent.knowledge_client = StubKB()
    # 给一个必然不存在的路径，逼 _env_status 走 shutil.which 分支
    agent.r_executor = StubExecutor("Rscript-not-installed-on-this-machine")
    agent.config = {}
    monkeypatch.setattr("shutil.which", lambda name: "C:/R/bin/Rscript.exe")

    body = _client(agent).get("/api/sidebar").json()

    assert body["env"]["rscript"] == "C:/R/bin/Rscript.exe"


def test_sidebar_soft_warn_uses_session_context(monkeypatch):
    from src.control import compact

    agent = FakeAgent()
    agent.knowledge_client = StubKB()
    agent.r_executor = StubExecutor("Rscript-not-installed-on-this-machine")
    agent.config = {}
    monkeypatch.setattr(compact, "should_soft_warn", lambda summary, window: True)
    client = _client(agent)
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]

    body = client.get(f"/api/sidebar?session_id={sid}").json()

    assert body["soft_warn"] is True


def test_sidebar_soft_warn_false_for_unknown_session(monkeypatch):
    agent = FakeAgent()
    agent.knowledge_client = StubKB()
    agent.r_executor = StubExecutor("Rscript-not-installed-on-this-machine")
    agent.config = {}
    monkeypatch.setattr("shutil.which", lambda name: None)

    body = _client(agent).get("/api/sidebar?session_id=nope").json()

    assert body["soft_warn"] is False


def test_main_module_targets_port_8600():
    module = importlib.import_module("src.api.__main__")

    assert module.HOST == "127.0.0.1"
    assert module.PORT == 8600


def test_open_browser_later_never_raises(monkeypatch):
    module = importlib.import_module("src.api.__main__")

    def boom(url):
        raise RuntimeError("no browser")

    monkeypatch.setattr(module.webbrowser, "open", boom)
    module._open_browser_later("http://127.0.0.1:8600", delay=0)

    for thread in threading.enumerate():
        if thread.name == "open-browser":
            thread.join(timeout=2)
