"""Ollama 自动唤起/关闭的单测：全部用假依赖，不真起进程、不发真实 HTTP。"""
from __future__ import annotations

import itertools
import subprocess
import time

import pytest

from src.knowledge import ollama_runtime as rt


class FakeProc:
    """假 Popen：记录行为，poll 返回值可配置。"""

    def __init__(
        self,
        argv: list[str],
        pid: int = 4242,
        terminate_sets_rc: bool = True,
        wait_rc: int = 0,
    ):
        self.argv = argv
        self.pid = pid
        self.terminate_sets_rc = terminate_sets_rc
        self.wait_rc = wait_rc
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
        return self.wait_rc


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    """每个用例前重置模块状态，并冻结配置读取、探活与回收线程（单测不发真实 HTTP）。

    五个注入点 `_probe`/`_which`/`_sleep`/`_spawn`/`_monotonic` 全部由夹具兜底冻结：
    任何未自行 stub 的用例都不可能起真实进程、也不可能忙等；
    各用例可在测试体内自行 monkeypatch 覆盖（测试体在夹具之后执行）。
    """
    rt.reset_for_tests()
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(rt, "_which", lambda name: None)
    monkeypatch.setattr(rt, "_sleep", lambda seconds: None)
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: None)
    # 兜底：夹具冻结 _spawn/_monotonic，未自行 stub 的用例不可能起真实进程，
    # 且 _wait_ready 的轮询会因时钟推进而立刻结束（不会忙等 30s）。
    # 各用例可在测试体内自行 monkeypatch 覆盖（测试体在夹具之后执行）。
    monkeypatch.setattr(rt, "_spawn", lambda argv, flags: FakeProc(argv))
    ticks = itertools.count(step=1.0)
    monkeypatch.setattr(rt, "_monotonic", lambda: next(ticks))
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


def test_ensure_ready_starts_ollama_and_waits_until_ready(monkeypatch):
    """探活失败 → 拉起 ollama serve → 轮询到就绪 → state=ready 且 managed=True。"""
    calls: list[list[str]] = []
    # 三次探活：启动前(None) → _wait_ready 轮询成功 → 启动后重探确认模型
    probes = [
        None,
        {"models": [{"name": "bge-m3:latest"}]},
        {"models": [{"name": "bge-m3:latest"}]},
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(
        rt, "_spawn", lambda argv, flags: calls.append(argv) or FakeProc(argv)
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    assert calls == [["ollama", "serve"]]
    assert rt.status() == {"state": "ready", "managed": True, "detail": ""}


def test_ensure_ready_times_out_and_terminates_started_process(monkeypatch):
    """启动后一直探活不到 → 终止自启进程并标 failed。"""
    procs: list[FakeProc] = []

    def fake_spawn(argv, flags):
        proc = FakeProc(argv)
        procs.append(proc)
        return proc

    clock = {"t": 0.0}

    def fake_clock():
        clock["t"] += 1.0
        return clock["t"]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: None)
    monkeypatch.setattr(rt, "_spawn", fake_spawn)
    # 每次轮询推进 1 秒，超过 START_TIMEOUT=30 → 必然超时
    monkeypatch.setattr(rt, "_monotonic", fake_clock)

    rt.ensure_ready()

    assert procs and procs[0].terminated is True
    assert rt.status()["state"] == "failed"
    assert "超时" in rt.status()["detail"]


def test_ensure_ready_pulls_model_when_missing(monkeypatch):
    """就绪但模型不在列表 → 执行 ollama pull。"""
    calls: list[list[str]] = []
    probes = [
        None,                        # 启动前探活失败
        {"models": []},              # 启动后轮询第一次就成功（空模型列表）
        {"models": []},              # 拉取后再探活
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(
        rt, "_spawn", lambda argv, flags: calls.append(argv) or FakeProc(argv)
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    assert calls == [["ollama", "serve"], ["ollama", "pull", "bge-m3:latest"]]
    assert rt.status()["state"] == "ready"


def test_ensure_ready_skips_pull_when_model_present(monkeypatch):
    """模型已在列表中 → 不触发 pull。"""
    calls: list[list[str]] = []
    tags = {"models": [{"name": "bge-m3:latest"}]}
    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: tags)
    monkeypatch.setattr(
        rt, "_spawn", lambda argv, flags: calls.append(argv) or FakeProc(argv)
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    assert calls == []          # 首次探活就成功，根本没启动
    assert rt.status()["managed"] is False


def test_ensure_ready_marks_unavailable_without_executable(monkeypatch):
    """找不到可执行文件 → unavailable，且不启动任何进程。"""
    monkeypatch.setattr(rt, "_which", lambda name: None)
    monkeypatch.setattr(rt.os, "name", "posix")     # 跳过 Windows 兜底
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: None)
    monkeypatch.setattr(rt, "_spawn", lambda argv, flags: pytest.fail("不应启动"))

    rt.ensure_ready()

    status = rt.status()
    assert status["state"] == "unavailable"
    assert status["managed"] is False
    assert "未找到" in status["detail"]


def test_concurrent_ensure_ready_starts_single_process(monkeypatch):
    """8 线程并发首次调用 → ollama serve 只被启动一次。"""
    import threading as th

    calls: list[list[str]] = []
    lock = th.Lock()
    started = th.Event()

    def fake_spawn(argv, flags):
        # 放大竞态窗口：持 _start_lock 期间 sleep，假锁下多线程会重复 spawn
        time.sleep(0.01)
        with lock:
            calls.append(argv)
        started.set()
        return FakeProc(argv)

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")

    def fake_probe(host, timeout):
        # 启动前失败；spawn 发生后视为就绪
        return {"models": [{"name": "bge-m3:latest"}]} if started.is_set() else None

    monkeypatch.setattr(rt, "_probe", fake_probe)
    monkeypatch.setattr(rt, "_spawn", fake_spawn)
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    threads = [th.Thread(target=rt.ensure_ready) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not any(t.is_alive() for t in threads), "有线程卡死（锁获取/流程挂起）"
    assert calls == [["ollama", "serve"]]


def test_ensure_ready_marks_failed_when_pull_exits_nonzero(monkeypatch):
    """ollama pull 退出码非 0（模型不存在等）→ failed，绝不进 ready。"""
    calls: list[list[str]] = []
    probes = [
        None,                        # 启动前探活失败
        {"models": []},              # _wait_ready 轮询成功
        {"models": []},              # 模型检查：仍缺 → pull（rc=1）
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(
        rt,
        "_spawn",
        lambda argv, flags: calls.append(argv) or FakeProc(argv, wait_rc=1),
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    assert calls == [["ollama", "serve"], ["ollama", "pull", "bge-m3:latest"]]
    status = rt.status()
    assert status["state"] == "failed"
    assert "退出码 1" in status["detail"]


def test_ensure_ready_retries_pull_when_model_still_missing(monkeypatch):
    """上次 pull 失败后，本次探活成功但模型仍缺 → 自管进程补拉，不误标 ready。"""
    calls: list[list[str]] = []
    probes = [
        None, {"models": []}, {"models": []},   # 第一轮：启动 → 轮询 → 缺模型 → pull 失败
        {"models": []},                          # 第二轮：探活成功（模型仍缺）
        {"models": []},                          # 第二轮：模型检查 → 再 pull（仍失败）
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(
        rt,
        "_spawn",
        lambda argv, flags: calls.append(argv) or FakeProc(argv, wait_rc=1),
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()
    assert rt.status()["state"] == "failed"       # 第一轮 pull rc=1

    rt.ensure_ready()                              # 第二轮：模型仍缺必须重试 pull

    assert calls == [
        ["ollama", "serve"],
        ["ollama", "pull", "bge-m3:latest"],
        ["ollama", "pull", "bge-m3:latest"],
    ]
    status = rt.status()
    assert status["state"] == "failed"
    assert "退出码" in status["detail"]


def test_ensure_ready_probe_wins_when_autostart_disabled(monkeypatch):
    """AUTOSTART=0 但用户自己的 Ollama 在跑 → 探活成功仍 ready（设计 §5：先探活后判门）。"""
    monkeypatch.setattr(rt, "_autostart", lambda: False)
    monkeypatch.setattr(
        rt, "_probe", lambda host, timeout: {"models": [{"name": "bge-m3:latest"}]}
    )
    monkeypatch.setattr(rt, "_spawn", lambda argv, flags: pytest.fail("不应启动"))

    rt.ensure_ready()

    assert rt.status() == {"state": "ready", "managed": False, "detail": ""}


def test_autostart_disabled_marks_unavailable_without_spawn(monkeypatch):
    """AUTOSTART=0 且无实例 → unavailable 且不 spawn（设计 §9）。"""
    monkeypatch.setattr(rt, "_autostart", lambda: False)
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: None)
    monkeypatch.setattr(rt, "_spawn", lambda argv, flags: pytest.fail("不应启动"))

    rt.ensure_ready()

    status = rt.status()
    assert status["state"] == "unavailable"
    assert status["managed"] is False
    assert "自动唤起已关闭" in status["detail"]


def test_terminate_owned_kills_when_terminate_raises():
    """terminate() 抛错 → except 里 kill 兜底，进程不因摘账而失管。"""

    class ExplodingProc(FakeProc):
        def terminate(self):
            raise RuntimeError("boom")

    proc = ExplodingProc(["ollama", "serve"])
    rt._proc, rt._managed = proc, True     # 直接注入自管进程账目

    rt._terminate_owned()

    assert proc.killed is True
    assert rt.status() == {"state": "stopped", "managed": False, "detail": ""}
