"""Ollama 自动唤起/关闭的单测：全部用假依赖，不真起进程、不发真实 HTTP。"""
from __future__ import annotations

import subprocess

import pytest

from src.knowledge import ollama_runtime as rt


class FakeProc:
    """假 Popen：记录行为，poll 返回值可配置。"""

    def __init__(self, argv: list[str], pid: int = 4242, terminate_sets_rc: bool = True):
        self.argv = argv
        self.pid = pid
        self.terminate_sets_rc = terminate_sets_rc
        self.terminated = False
        self.killed = False
        self.waited = False
        self._returncode = None

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        if self.terminate_sets_rc:
            self._returncode = -15

    def kill(self):
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        self.waited = True
        return 0


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    """每个用例前重置模块状态，并冻结配置读取、探活与回收线程（单测不发真实 HTTP）。"""
    rt.reset_for_tests()
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(rt, "_which", lambda name: None)
    monkeypatch.setattr(rt, "_sleep", lambda seconds: None)
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: None)
    yield
    rt.reset_for_tests()


def test_status_shape_has_exactly_three_keys():
    rt.ensure_ready()  # 探活被夹具 stub 为失败：找到 exe 则保持 stopped（T1 不启动），找不到则 unavailable
    status = rt.status()
    assert set(status) == {"state", "managed", "detail"}
    assert isinstance(status["state"], str)
    assert isinstance(status["managed"], bool)
    assert isinstance(status["detail"], str)


def test_ensure_ready_marks_ready_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: {"models": []})

    rt.ensure_ready()

    status = rt.status()
    assert status["state"] == "ready"
    assert status["managed"] is False
    assert status["detail"] == ""


def test_ensure_ready_never_manages_external_instance(monkeypatch):
    """探活成功即判定为用户自己的 Ollama：managed 恒 False。"""
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: {"models": []})
    monkeypatch.setattr(rt, "_spawn", lambda argv, flags: pytest.fail("不应启动进程"))

    rt.ensure_ready()

    assert rt.status() == {"state": "ready", "managed": False, "detail": ""}


def test_find_exe_prefers_path_lookup(monkeypatch):
    monkeypatch.setattr(rt, "_which", lambda name: "C:/tools/ollama.exe")

    assert rt._find_exe() == "C:/tools/ollama.exe"


def test_find_exe_falls_back_to_windows_install_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_which", lambda name: None)
    monkeypatch.setattr(rt.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("x", encoding="utf-8")

    assert rt._find_exe() == str(exe)


def test_model_name_gets_latest_tag(monkeypatch):
    from src import config

    monkeypatch.setattr(config, "EMBEDDING_MODEL", "bge-m3")
    assert rt._model() == "bge-m3:latest"
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "qwen3-embed:4b")
    assert rt._model() == "qwen3-embed:4b"


def test_spawn_default_uses_devnull_and_no_window_flag(monkeypatch):
    """默认 _spawn 必须吞掉子进程输出且不弹窗（Windows），不真起进程。

    Popen.stdout 仅在 stdout=PIPE 时为流对象，DEVNULL 时恒 None，
    故改为捕获构造 kwargs 断言，而非检查返回对象的属性。
    """
    captured: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured.update(kwargs)

    monkeypatch.setattr(rt.subprocess, "Popen", FakePopen)

    rt._spawn_default(["ollama", "serve"], subprocess.CREATE_NO_WINDOW)

    assert captured["argv"] == ["ollama", "serve"]
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert captured["creationflags"] == subprocess.CREATE_NO_WINDOW
