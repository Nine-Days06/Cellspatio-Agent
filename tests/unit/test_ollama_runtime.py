"""Ollama 自动唤起/关闭的单测：全部用假依赖，不真起进程、不发真实 HTTP。"""
from __future__ import annotations

import itertools
import subprocess
import time

import pytest

from src import config
from src.knowledge import ollama_runtime as rt

# clean_runtime 夹具会把 `_autostart`/`_idle_seconds` 冻结成桩（True / 0.0）。
# 需要验证「真实配置 → 真实阈值」这条链路的用例必须能拿回真函数，因此在导入期
# 就把原函数引用存下来（此时夹具还没生效）。
_REAL_AUTOSTART = rt._autostart
_REAL_IDLE_SECONDS = rt._idle_seconds
_REAL_EAGER_START = rt._eager_start


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
    """就绪但模型不在列表且 OLLAMA_AUTO_PULL=1 → 执行 ollama pull。"""
    calls: list[list[str]] = []
    probes = [
        None,                        # 启动前探活失败
        {"models": []},              # 启动后轮询第一次就成功（空模型列表）
        {"models": []},              # 拉取后再探活
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(rt, "_auto_pull", lambda: True)  # 1.6 起默认关；本用例测 pull 机制
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
    monkeypatch.setattr(rt, "_auto_pull", lambda: True)  # 1.6 起默认关；本用例测 rc 校验
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
        {"models": []},                          # 第二轮：探活成功（模型仍缺）；该结果直接
        #                                              复用给 _ensure_model，不再发第二次 GET
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(rt, "_auto_pull", lambda: True)  # 1.6 起默认关；本用例测补拉重试
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


def test_touch_updates_last_used(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(rt, "_monotonic", lambda: clock["t"])
    rt.touch()
    assert rt._last_used == 100.0
    clock["t"] = 130.0
    rt.touch()
    assert rt._last_used == 130.0


def test_maybe_reap_closes_managed_process_when_idle(monkeypatch):
    """空闲超阈值且是自己启动的 → terminate。"""
    proc = FakeProc(["ollama", "serve"])
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(rt, "_monotonic", lambda: 1000.0)
    rt._state = "ready"                 # 回收门：只在 ready 状态回收
    rt._last_used = 1000.0 - 601        # 已空闲 601 秒
    rt._proc = proc
    rt._managed = True

    assert rt.maybe_reap() is True
    assert proc.terminated is True
    # detail 不能被清空：sidebar 上要能看到「为什么停了」
    assert rt.status() == {
        "state": "stopped",
        "managed": False,
        "detail": "空闲 10 分钟，已关闭",
    }


def test_maybe_reap_keeps_warm_when_eager_start_on(monkeypatch):
    """随程序启动模式下不得空闲关闭（端到端：走真实 _idle_seconds/_eager_start）。

    **必须先解除 clean_runtime 对这两个函数的冻结**：夹具把 `_idle_seconds` stub 成
    0.0、`_autostart` stub 成 True，若不还原，本用例会因「阈值本来就是 0」而通过，
    变成假绿——根本验证不到 eager 逻辑。
    """
    proc = FakeProc(["ollama", "serve"])
    monkeypatch.setattr(rt, "_idle_seconds", _REAL_IDLE_SECONDS)
    monkeypatch.setattr(rt, "_eager_start", _REAL_EAGER_START)
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", True)
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 10)   # 开着，但被 eager 压制
    monkeypatch.setattr(rt, "_monotonic", lambda: 1000.0)
    rt._state = "ready"
    rt._last_used = 1000.0 - 99999       # 空闲远超 10 分钟
    rt._proc = proc
    rt._managed = True

    assert rt.maybe_reap() is False
    assert proc.terminated is False
    assert rt.status()["managed"] is True


def test_maybe_reap_still_reaps_when_eager_off(monkeypatch):
    """反向对照：未开启 eager 时，真实 _idle_seconds 仍按分钟数回收。

    没有这条，上一条的「不回收」就无法归因于 eager 而不是别的因素。
    """
    proc = FakeProc(["ollama", "serve"])
    monkeypatch.setattr(rt, "_idle_seconds", _REAL_IDLE_SECONDS)
    monkeypatch.setattr(rt, "_eager_start", _REAL_EAGER_START)
    monkeypatch.setattr(config, "OLLAMA_EAGER_START", False)
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 10)
    monkeypatch.setattr(rt, "_monotonic", lambda: 1000.0)
    rt._state = "ready"
    rt._last_used = 1000.0 - 601
    rt._proc = proc
    rt._managed = True

    assert rt.maybe_reap() is True
    assert proc.terminated is True


def test_warm_up_async_skipped_when_eager_off(monkeypatch):
    """默认（未开启）不得起预热线程——零行为变更。"""
    from src import config

    monkeypatch.setattr(config, "OLLAMA_EAGER_START", False)
    started = []
    monkeypatch.setattr(rt.threading, "Thread", lambda **kw: started.append(kw) or _FakeThread())

    assert rt.warm_up_async() is False
    assert started == []


def test_warm_up_async_starts_daemon_thread_when_eager_on(monkeypatch):
    """开启后起守护线程跑 ensure_ready，且不阻塞调用方。"""
    monkeypatch.setattr(rt, "_eager_start", lambda: True)
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    calls = []
    monkeypatch.setattr(rt, "ensure_ready", lambda: calls.append("ready"))
    captured = {}

    def fake_thread(**kwargs):
        captured.update(kwargs)
        return _FakeThread(**kwargs)

    monkeypatch.setattr(rt.threading, "Thread", fake_thread)

    assert rt.warm_up_async() is True
    assert captured["target"] is rt.ensure_ready
    assert captured.get("daemon") is True
    assert captured.get("name") == "ollama-warmup"
    assert calls == ["ready"]          # 守护线程 start() 立即执行了目标


def test_warm_up_async_skipped_when_autostart_disabled(monkeypatch):
    """用户既关了自动唤起又开了预热 → 以自动唤起为准（不越权拉起）。

    必须 patch `rt._autostart` 而非 config：clean_runtime 已把它 stub 成 True，
    patch config 够不到被替换的函数。
    """
    monkeypatch.setattr(rt, "_eager_start", lambda: True)
    monkeypatch.setattr(rt, "_autostart", lambda: False)
    started = []
    monkeypatch.setattr(rt.threading, "Thread", lambda **kw: started.append(kw) or _FakeThread(**kw))

    assert rt.warm_up_async() is False
    assert started == []


class _FakeThread:
    """最小 Thread 替身：start() 立即同步执行 target，便于断言「预热确实跑了」。"""

    def __init__(self, target=None, name=None, daemon=None, **kwargs) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True
        if self.target is not None:
            self.target()

    def join(self, timeout: float | None = None) -> None:
        return None


def test_maybe_reap_never_closes_external_instance(monkeypatch):
    """外部实例（managed=False）即便空闲也不关。"""
    proc = FakeProc(["ollama", "serve"])
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 1.0)
    monkeypatch.setattr(rt, "_monotonic", lambda: 1000.0)
    rt._last_used = 0.0
    rt._proc = proc
    rt._managed = False

    assert rt.maybe_reap() is False
    assert proc.terminated is False
    assert proc.killed is False


def test_maybe_reap_respects_zero_idle_minutes(monkeypatch):
    """阈值为 0（不自动关闭）时永不关闭。"""
    proc = FakeProc(["ollama", "serve"])
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 0.0)
    rt._last_used = 0.0
    rt._proc = proc
    rt._managed = True

    assert rt.maybe_reap() is False
    assert proc.terminated is False


def test_shutdown_if_managed_only_terminates_owned_process(monkeypatch):
    """退出钩子：自有进程 terminate，外部进程不动。"""
    owned = FakeProc(["ollama", "serve"])
    external = FakeProc(["ollama", "serve"])
    rt._proc = owned
    rt._managed = True
    rt.shutdown_if_managed()
    assert owned.terminated is True

    rt._proc = external
    rt._managed = False
    rt.shutdown_if_managed()
    assert external.terminated is False


def test_ensure_reaper_skipped_when_idle_minutes_zero():
    """阈值为 0 时不创建回收线程（避免常驻线程空转）。"""
    rt._reaper_started = False
    rt._ensure_reaper()
    assert rt._reaper_started is False


def test_ensure_reaper_starts_thread_once(monkeypatch):
    """阈值为正时创建一次 daemon 回收线程。"""
    started: list[str] = []
    created: list[tuple[str, bool]] = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            started.append(name)
            created.append((name, daemon))
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append("started")

    monkeypatch.setattr(rt.threading, "Thread", FakeThread)
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 600.0)
    rt._reaper_started = False
    rt._ensure_reaper()
    rt._ensure_reaper()          # 第二次不应重复创建

    assert started == ["ollama-reaper", "started"]
    assert rt._reaper_started is True
    # daemon=True 是唯一安全阀：_reaper_loop 循环体无 break/return，被改成 False
    # 会让应用退出时永久挂死（本特性最难排查的失败模式）
    assert created == [("ollama-reaper", True)]


def test_ensure_ready_fails_when_managed_but_executable_missing(monkeypatch):
    """自启进程在跑、但找不到 exe → 标 failed，绝不误报 ready。

    回归守卫：`ensure_ready` 的 managed 分支拿不到 exe 时无法确认模型是否就位，
    以前会静默落到后面标 ready（模型仍缺却报可用），用户要到下游嵌入才炸。
    """
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: {"models": []})
    monkeypatch.setattr(rt, "_which", lambda name: None)
    monkeypatch.setattr(rt.os, "name", "posix")      # 跳过 Windows 兜底 → exe 必然 None
    monkeypatch.setattr(rt, "_spawn", lambda argv, flags: pytest.fail("不应启动或拉模型"))
    # 直接注入自管进程账目：探活已成功，流程不会再 spawn，用例只关心 exe 缺失路径
    rt._proc, rt._managed = FakeProc(["ollama", "serve"]), True

    rt.ensure_ready()

    status = rt.status()
    assert status["state"] == "failed"
    assert "未找到" in status["detail"]


# ── 回收线程不变量（审查 I-1/I-2/I-3、M-1/M-7/M-9 的回归锁）──


def test_ensure_ready_starts_reaper_on_recovery_path(monkeypatch):
    """恢复路径（上次 pull 失败、本次探活成功）也必须起回收线程。

    回归守卫（审查 I-1）：`_ensure_reaper` 只挂在「本次真的 spawn 了新进程」
    的分支末尾时，从未 spawn 的恢复路径拿不到空闲回收 —— 生产可达：首次
    `ollama pull` 因网络失败 → failed+managed=True，下次提问走探活成功路径。
    """
    created: list[tuple[str, bool]] = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            created.append((name, daemon))

        def start(self):
            pass

    monkeypatch.setattr(rt.threading, "Thread", FakeThread)
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(
        rt, "_probe", lambda host, timeout: {"models": [{"name": "bge-m3:latest"}]}
    )
    rt._state = "failed"                             # 不命中 ready 节流
    rt._proc, rt._managed = FakeProc(["ollama", "serve"]), True   # 上次已自启

    rt.ensure_ready()

    assert rt.status()["state"] == "ready"
    assert created == [("ollama-reaper", True)]
    assert rt._reaper_started is True


def test_maybe_reap_never_reaps_failed_managed_process(monkeypatch):
    """failed 状态（模型缺失等）不回收：否则洗掉失败原因 + 反复付冷启动。

    回归守卫（审查 I-2）：`_ensure_model` 失败只写 failed、不碰 `_proc`，
    而失败路径不刷新 `_last_used` → idle 远超阈值 → 回收线程把唯一的诊断
    信息（detail）擦成空，还让注定失败的 pull 按请求反复起进程。
    """
    proc = FakeProc(["ollama", "serve"])
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(rt, "_monotonic", lambda: 1000.0)
    rt._state, rt._detail = "failed", "拉取模型失败（退出码 1）"
    rt._last_used = 0.0             # 失败路径不刷新活跃时间
    rt._proc = proc
    rt._managed = True

    assert rt.maybe_reap() is False
    assert proc.terminated is False
    assert proc.killed is False
    # 失败原因必须原样留着给 sidebar
    assert rt.status() == {
        "state": "failed",
        "managed": True,
        "detail": "拉取模型失败（退出码 1）",
    }


def test_maybe_reap_takes_start_lock():
    """回收与启动互斥（审查 I-3 最关键的不变量）：`_start_lock` 被占时必须等。

    没有互斥时，60s 的回收 tick 会把正在 spawn/拉模型的进程 terminate 掉。
    """
    import threading as th

    finished = th.Event()
    rt._start_lock.acquire()
    reaper = th.Thread(target=lambda: (rt.maybe_reap(), finished.set()))
    reaper.start()
    try:
        assert finished.wait(0.2) is False, "未与启动流程互斥：回收线程直接开跑了"
    finally:
        rt._start_lock.release()     # 断言失败也必须放锁，否则线程永久卡死
    reaper.join(timeout=5)
    assert finished.is_set() is True
    assert reaper.is_alive() is False


def test_reap_does_not_interrupt_model_pull(monkeypatch):
    """模型下载期间回收必须让路（审查 I-3）：几分钟的 pull 不能被 tick 打断。"""
    import threading as th

    pull_started = th.Event()
    release_pull = th.Event()
    proc = FakeProc(["ollama", "serve"])

    def blocking_spawn(argv, flags):
        spawned = FakeProc(argv)
        if len(argv) > 1 and argv[1] == "pull":
            def slow_wait(timeout=None):
                pull_started.set()
                release_pull.wait(timeout=5)
                return 0
            spawned.wait = slow_wait
        return spawned

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: {"models": []})
    monkeypatch.setattr(rt, "_auto_pull", lambda: True)  # 1.6 起默认关；本用例测 pull 期间回收让路
    monkeypatch.setattr(rt, "_spawn", blocking_spawn)
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 600.0)
    monkeypatch.setattr(rt, "_monotonic", lambda: 10_000.0)
    rt._state = "failed"                                 # 不命中节流
    rt._last_used = 0.0                                  # idle=10000s，远超阈值
    rt._proc, rt._managed = proc, True                   # 自启进程正在被使用

    ensure_thread = th.Thread(target=rt.ensure_ready)
    ensure_thread.start()
    try:
        assert pull_started.wait(5) is True, "ensure_ready 没走到 pull"
        reap_finished = th.Event()
        reaper = th.Thread(target=lambda: (rt.maybe_reap(), reap_finished.set()))
        reaper.start()
        assert reap_finished.wait(0.2) is False, "pull 期间回收不该完成"
        assert proc.terminated is False, "pull 期间自启进程被误杀"
    finally:
        release_pull.set()
        ensure_thread.join(timeout=5)
    reaper.join(timeout=5)
    assert reap_finished.is_set() is True, "锁释放后回收应完成"


def test_terminate_owned_kills_when_wait_times_out():
    """terminate 后宽限期内没退出 → kill 强杀（审查 M-1 的新分支）。

    原实现是无条件盲睡 5s；改为 wait(timeout) 后必须覆盖超时强杀这条路径。
    """
    class StubbornProc(FakeProc):
        def wait(self, timeout=None):
            self.waited = True
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd="ollama", timeout=timeout or 0)
            return -9

    proc = StubbornProc(["ollama", "serve"])
    rt._proc, rt._managed = proc, True

    rt._terminate_owned()

    assert proc.terminated is True
    assert proc.killed is True
    assert rt.status()["managed"] is False


def test_ensure_reaper_rolls_back_when_thread_start_fails(monkeypatch):
    """线程起不来必须回滚 `_reaper_started`（审查 M-7）。

    否则空闲回收永久静默失效，且没有任何报错——最难排查的失败模式。
    """
    class ExplodingThread:
        def __init__(self, target, name, daemon):
            self.daemon = daemon

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(rt.threading, "Thread", ExplodingThread)
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 600.0)
    rt._reaper_started = False

    rt._ensure_reaper()

    assert rt._reaper_started is False


def test_reset_for_tests_clears_all_state():
    """单测隔离钩子必须清干净全部模块状态（设计 §9，此前无直接测试）。"""
    rt._state, rt._detail = "ready", "某个 detail"
    rt._managed, rt._proc = True, FakeProc(["ollama", "serve"])
    rt._last_used = 1234.5
    rt._last_probe_ok = 99.0
    rt._reaper_started = True

    rt.reset_for_tests()

    assert rt._state == "stopped"
    assert rt._detail == ""
    assert rt._managed is False
    assert rt._proc is None
    assert rt._last_used == 0.0
    assert rt._last_probe_ok == -1e9
    assert rt._reaper_started is False


# ── T1 追加：needs_model 终态与自动拉取开关 ───────────────────────────


def test_ensure_model_needs_model_when_auto_pull_disabled(monkeypatch):
    """缺模型且 OLLAMA_AUTO_PULL=0 → needs_model 且不触发 pull。"""
    calls: list[list[str]] = []
    probes = [
        None,  # 启动前探活失败
        {"models": []},  # 启动后轮询成功
        {"models": []},  # _ensure_model 自探：模型仍缺
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    monkeypatch.setattr(rt, "_auto_pull", lambda: False)
    monkeypatch.setattr(
        rt, "_spawn", lambda argv, flags: calls.append(argv) or FakeProc(argv)
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    # 只启动 serve，不 pull
    assert calls == [["ollama", "serve"]]
    status = rt.status()
    assert status["state"] == "needs_model"
    assert "bge-m3" in status["detail"]
    assert "OLLAMA_AUTO_PULL" in status["detail"]
    assert "重启" in status["detail"]  # 提醒改环境变量需重启


def test_ensure_model_pulls_when_auto_pull_enabled(monkeypatch):
    """缺模型但 OLLAMA_AUTO_PULL=1 → 走 pull 流程。"""
    calls: list[list[str]] = []
    probes = [
        None,  # 启动前探活失败
        {"models": []},  # 启动后轮询成功
        {"models": []},  # 拉取后再探活
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    monkeypatch.setattr(rt, "_auto_pull", lambda: True)
    monkeypatch.setattr(
        rt, "_spawn", lambda argv, flags: calls.append(argv) or FakeProc(argv)
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    assert calls == [["ollama", "serve"], ["ollama", "pull", "bge-m3:latest"]]
    assert rt.status()["state"] == "ready"


def test_ensure_model_pull_failed_returns_failed(monkeypatch):
    """拉取 rc≠0 → failed（回归保护，计划 1.5 已修的 C1 不得退化）。"""
    calls: list[list[str]] = []
    probes = [
        None,  # 启动前探活失败
        {"models": []},  # 启动后轮询成功
        {"models": []},  # 模型检查：仍缺 → pull（rc=1）
    ]

    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    monkeypatch.setattr(rt, "_auto_pull", lambda: True)
    monkeypatch.setattr(
        rt, "_spawn", lambda argv, flags: calls.append(argv) or FakeProc(argv, wait_rc=1)
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    assert calls == [["ollama", "serve"], ["ollama", "pull", "bge-m3:latest"]]
    status = rt.status()
    assert status["state"] == "failed"
    assert "退出码" in status["detail"]


def test_ensure_model_skips_pull_when_model_present(monkeypatch):
    """模型已在列表中 → 不 pull（回归保护）。"""
    calls: list[list[str]] = []
    tags = {"models": [{"name": "bge-m3:latest"}]}
    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: tags)
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    monkeypatch.setattr(rt, "_auto_pull", lambda: True)
    monkeypatch.setattr(
        rt, "_spawn", lambda argv, flags: calls.append(argv) or FakeProc(argv)
    )
    monkeypatch.setattr(rt, "_monotonic", lambda: 0.0)

    rt.ensure_ready()

    assert calls == []  # 首次探活就成功，根本没启动
    assert rt.status()["managed"] is False


def test_spawn_log_includes_models_dir(monkeypatch, caplog):
    """启动日志含模型目录：设了 OLLAMA_MODELS 打其值，未设打提示。"""
    import logging

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(rt, "_which", lambda name: "ollama")
    monkeypatch.setattr(rt, "_autostart", lambda: True)
    monkeypatch.setattr(rt, "_auto_pull", lambda: False)  # 只看启动日志，不触发 pull
    monkeypatch.setattr(rt, "_spawn", lambda argv, flags: FakeProc(argv))

    def started_messages() -> list[str]:
        return [r.getMessage() for r in caplog.records if "ollama started" in r.getMessage()]

    # 未设 OLLAMA_MODELS → 打「默认目录」提示
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    probes = [None, {"models": []}, {"models": []}]
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))

    rt.ensure_ready()

    assert any("未设置→服务将使用默认目录" in msg for msg in started_messages())

    # 设了 OLLAMA_MODELS → 打其值
    monkeypatch.setenv("OLLAMA_MODELS", "D:/custom/models")
    rt.reset_for_tests()
    caplog.clear()
    probes = [None, {"models": []}, {"models": []}]
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: probes.pop(0))

    rt.ensure_ready()

    assert any("OLLAMA_MODELS=D:/custom/models" in msg for msg in started_messages())


def test_needs_model_is_valid_status(monkeypatch):
    """needs_model 是合法状态：status() 返回三键且 state="needs_model"（不破坏三键契约）。"""
    monkeypatch.setattr(rt, "_probe", lambda host, timeout: {"models": []})
    monkeypatch.setattr(rt, "_auto_pull", lambda: False)

    assert rt._ensure_model("http://127.0.0.1:11434", "ollama", 0) is False

    status = rt.status()
    assert set(status) == {"state", "managed", "detail"}
    assert status["state"] == "needs_model"
