# Ollama 自动唤起与自动关闭 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让本地 Ollama（唯一的 embedding 后端）做到「用到时自动唤起、用完后自动关闭」，且只管理本应用自己启动的进程。

**架构：** 新增单模块 `src/knowledge/ollama_runtime.py` 负责进程保活与回收；在 `llm_factory.build_embedding_func()` 内用 async 包裹层拦截每次 embedding 调用，先 `ensure_ready()` 再委托原始实现；`/api/sidebar` 的 `env` 增加 `ollama` 状态字段供前端展示。所有失败只转状态与日志，绝不抛到对话层。

**技术栈：** Python 3.12、stdlib（`subprocess`/`urllib.request`/`threading`/`atexit`）、pytest、ruff。不新增第三方依赖。

---

## 基线与硬门禁

| 项 | 值 |
|---|---|
| 后端基线 | `python -m pytest tests/ -q` = **469 passed, 7 skipped**（只增不减） |
| ruff | 改动文件零告警（`ruff check <改动文件>`） |
| 禁止 | 裸 `pytest --collect-only`（会递归 `.worktrees/` 报 5220 收集错误） |
| 环境 | Windows / PowerShell / Python 3.12；本机 ollama 已在 PATH（`C:\Users\22813\AppData\Local\Programs\Ollama\ollama.exe`），环境已有 `OLLAMA_HOST=http://127.0.0.1:11434` |
| 分支 | `main`（HEAD `8b11c26`，已推送） |

新增测试合计 **30 个**（T1 7 + T2 6 + T3 7 + T4 8 + T5 2），全量预期 **499 passed, 7 skipped**。

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/knowledge/ollama_runtime.py` | 创建 | 进程保活与回收：探活、按需启动、拉模型、空闲回收、状态上报 |
| `src/knowledge/llm_factory.py:51-77` | 修改 | `build_embedding_func` 内加 async 包裹层（先 ensure 再委托） |
| `src/config.py:102-103` | 修改 | 新增 `OLLAMA_AUTOSTART`、`OLLAMA_IDLE_MINUTES` |
| `.env.example:39-40` | 修改 | 两个新键的注释说明 |
| `src/api/routes_meta.py:63` | 修改 | `env` 返回值新增 `ollama` 字段 |
| `tests/unit/test_ollama_runtime.py` | 创建 | 生命周期单测（假 Popen + 假探活，不真起进程/不发真实 HTTP） |
| `tests/unit/test_llm_factory.py` | 修改 | 包裹层增补用例 |
| `tests/unit/test_api_meta.py` | 修改 | `env.ollama` 增补用例 |

**不动**：`src/control/`、`src/api/routes_chat.py`、`src/api/routes_sessions.py`、`src/ui/`、前端、其他既有测试。

## 契约速查（实现时对照）

**模块接口**（`src/knowledge/ollama_runtime.py`，全部模块级函数）

| 接口 | 签名 | 说明 |
|---|---|---|
| `ensure_ready` | `() -> None` | 幂等；失败不抛，只转状态与日志 |
| `touch` | `() -> None` | 标记最近使用，供空闲判定 |
| `status` | `() -> dict` | `{"state": str, "managed": bool, "detail": str}` |
| `maybe_reap` | `() -> bool` | 空闲超阈值则关闭自有进程，返回是否执行了关闭 |
| `shutdown_if_managed` | `() -> None` | 进程退出钩子，仅终止自有进程 |
| `reset_for_tests` | `() -> None` | 清空模块级状态，供单测隔离 |

**可注入依赖**（单测替换）

| 引用 | 签名 | 默认实现 |
|---|---|---|
| `_probe` | `(host: str, timeout: float) -> dict \| None` | stdlib `urllib` GET `{host}/api/tags`，成功返回 JSON，失败/超时返回 `None` |
| `_spawn` | `(argv: list[str], creationflags: int) -> Popen` | `subprocess.Popen(argv, creationflags=..., stdout=DEVNULL, stderr=DEVNULL)` |
| `_which` | `(name: str) -> str \| None` | `shutil.which` |
| `_monotonic` | `() -> float` | `time.monotonic` |
| `_sleep` | `(seconds: float) -> None` | `time.sleep` |

**状态机**：`ready` / `starting` / `downloading` / `idle` / `stopped` / `unavailable` / `failed`。

**配置**：`OLLAMA_AUTOSTART`（默认 1=开）、`OLLAMA_IDLE_MINUTES`（默认 10，`<=0` 表示不自动关闭）；沿用 `OLLAMA_URL`/`OLLAMA_HOST`/`EMBEDDING_MODEL`。

**关键常量**：`PROBE_TIMEOUT=1.0`、`START_TIMEOUT=30.0`、`POLL_INTERVAL=0.5`、`THROTTLE_WINDOW=5.0`、`TERMINATE_GRACE=5.0`、`REAP_INTERVAL=60.0`。

**host 归一化**：复用 `LightRAGClient._normalize_ollama_host`（`src/knowledge/lightrag_client.py:122-133`），**延迟 import**，失败时退回原始值，不重复实现该逻辑。

---

### 任务 T1：`ollama_runtime` 骨架（注入点 + 状态 + 探活）

**文件：**
- 创建：`src/knowledge/ollama_runtime.py`
- 创建：`tests/unit/test_ollama_runtime.py`

- [ ] **步骤 1：编写失败的测试**

`tests/unit/test_ollama_runtime.py`：

```python
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/unit/test_ollama_runtime.py -v`

预期：全部 FAIL / ERROR，报 `ModuleNotFoundError: No module named 'src.knowledge.ollama_runtime'`（7 个用例，含步骤 1 里的 `test_spawn_default_uses_devnull_and_no_window_flag`）。

- [ ] **步骤 3：编写最少实现代码**

创建 `src/knowledge/ollama_runtime.py`：

```python
"""Ollama 进程自动唤起与自动关闭。

embedding 唯一后端是本地 Ollama（见 llm_factory.build_embedding_func）。
本模块负责：用到时按需拉起 `ollama serve`，空闲超过阈值后关闭**自己启动的**进程。

三条硬约束：
1. 绝不启动/关闭用户自己启动的 Ollama——探活成功即视为外部实例（managed=False）。
2. 任何失败都不抛到调用方，只转成 status() 的状态 + 日志（保持对话链路行为不变）。
3. 探活与启动均幂等：并发首次调用只会启动一个进程。
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 可注入依赖（单测替换，避免真起进程/发真实 HTTP）──


def _probe_default(host: str, timeout: float) -> dict[str, Any] | None:
    """GET {host}/api/tags；成功返回解析后的 JSON，失败或超时返回 None。"""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 探活失败一律视为不可用
        return None


def _spawn_default(argv: list[str], creationflags: int) -> subprocess.Popen:
    """起子进程：丢弃输出 + Windows 不弹窗。"""
    return subprocess.Popen(
        argv,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


_probe = _probe_default
_spawn = _spawn_default
_which = shutil.which
_monotonic = time.monotonic
_sleep = time.sleep

# ── 常量 ──
PROBE_TIMEOUT = 1.0
START_TIMEOUT = 30.0
POLL_INTERVAL = 0.5
THROTTLE_WINDOW = 5.0
TERMINATE_GRACE = 5.0
REAP_INTERVAL = 60.0

# ── 模块级状态（protected by _state_lock / _used_lock）──
_state_lock = threading.Lock()
_used_lock = threading.Lock()
_state = "stopped"
_detail = ""
_managed = False
_proc: subprocess.Popen | None = None
_last_used = 0.0
_last_probe_ok = -1e9
_reaper_started = False
# _reaper_stop：回收线程的休眠/退出开关。用 Event.wait() 而非 time.sleep()——
# 打桩 _sleep 的测试里 sleep 会变成零延迟空转（实测瞬时 20 万次迭代＝100% CPU），
# Event.wait 既是可中断的睡眠，又让 reset_for_tests 能立刻叫醒并停掉线程。
_reaper_stop = threading.Event()


def _autostart() -> bool:
    """是否允许自动唤起（配置缺失时按开启处理，与默认一致）。"""
    try:
        from src.config import OLLAMA_AUTOSTART

        return bool(OLLAMA_AUTOSTART)
    except Exception:  # noqa: BLE001 - 配置不可用时保持默认可用
        return True


def _idle_seconds() -> float:
    """空闲关闭阈值（秒）；<=0 表示不自动关闭。"""
    try:
        from src.config import OLLAMA_IDLE_MINUTES

        return max(0, int(OLLAMA_IDLE_MINUTES)) * 60
    except Exception:  # noqa: BLE001 - 配置不可用时不自动关闭
        return 0.0


def _model() -> str:
    """当前 embedding 模型名（Ollama 侧补 :latest tag）。"""
    from src.config import EMBEDDING_MODEL

    return EMBEDDING_MODEL if ":" in EMBEDDING_MODEL else f"{EMBEDDING_MODEL}:latest"


def _host() -> str:
    """Ollama 服务地址；延迟 import 复用 client 的归一化，失败退回原始值。"""
    raw = os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
    try:
        from src.knowledge.lightrag_client import LightRAGClient

        return LightRAGClient._normalize_ollama_host(raw)
    except Exception:  # noqa: BLE001 - 归一化不可用时用原值
        return raw


def _find_exe() -> str | None:
    """定位 ollama 可执行文件：先 PATH，再 Windows 默认安装目录。"""
    exe = _which("ollama")
    if exe:
        return exe
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            fallback = Path(local) / "Programs" / "Ollama" / "ollama.exe"
            if fallback.is_file():
                return str(fallback)
    return None


def status() -> dict[str, Any]:
    """当前 Ollama 状态：state / managed / detail，供侧边栏展示。

    这三个键被 T1 测试与设计 §6 锁定。若 sidebar 要展示「空闲剩余时间」，
    请新增独立访问器（如 `idle_for()`），不要往这里加第 4 个键。
    """
    with _state_lock:
        return {"state": _state, "managed": _managed, "detail": _detail}


def reset_for_tests() -> None:
    """清空模块级状态（单测隔离用，不终止任何进程）。"""
    global _state, _detail, _managed, _proc, _last_used, _last_probe_ok, _reaper_started
    with _state_lock:
        _proc = None
        _managed = False
        _state, _detail = "stopped", ""
        _last_probe_ok = -1e9
        _reaper_started = False
    with _used_lock:
        _last_used = 0.0
    # set() 叫醒并停掉上一轮可能还在跑的回收线程，clear() 让下一轮能重新起
    _reaper_stop.set()
    _reaper_stop.clear()
```

> 注：T1 只要求 `status/ensure_ready/reset_for_tests/_find_exe/_model/_spawn_default` 与常量就位。`ensure_ready()` 在 T1 里只做「探活 → ready / 探活失败且找不到 exe → unavailable」，启动分支在 T2 补齐；`touch`/`maybe_reap`/`shutdown_if_managed` 与 `atexit.register` 在 T3 定义与注册。**T1 不要写 `atexit.register(shutdown_if_managed)`**——该函数此时尚未定义，导入期即 NameError。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/unit/test_ollama_runtime.py -v`

预期：7 个用例全部 PASS（`test_spawn_default_uses_devnull_and_no_window_flag` 已改为 monkeypatch 捕获 kwargs，不真起进程、无 skip 分支，故无 `skipped`）。ruff：`ruff check src/knowledge/ollama_runtime.py tests/unit/test_ollama_runtime.py` → `All checks passed!`

- [ ] **步骤 5：Commit**

```powershell
git add src/knowledge/ollama_runtime.py tests/unit/test_ollama_runtime.py
git commit -m "feat: 新增 Ollama 生命周期模块骨架（注入点、状态与探活）"
```

### 任务 T2：按需启动、模型拉取与并发去重

**文件：**
- 修改：`src/knowledge/ollama_runtime.py`（替换 T1 的 `ensure_ready` 占位实现，新增 `_wait_ready`/`_has_model`）
- 修改：`tests/unit/test_ollama_runtime.py`（追加 6 个用例）

- [ ] **步骤 1：编写失败的测试**

追加到 `tests/unit/test_ollama_runtime.py` 末尾：

```python
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/unit/test_ollama_runtime.py -v`

预期：T1 的 7 个仍 PASS（或 1 skipped），新增 6 个中 4 个 FAIL——`test_ensure_ready_starts_ollama_and_waits_until_ready` 断言 `calls == [["ollama","serve"]]` 实际为 `[]`（T1 的 `ensure_ready` 探活失败后只标 `unavailable`）、超时/pull/并发用例同因不启动而失败；`skips_pull`/`no_executable` 两例恰被 T1 行为满足先过。实测 `4 failed, 9 passed`。

- [ ] **步骤 3：编写最少实现代码**

在 T1 已有的 `ensure_ready()` 占位实现（探活成功 → `ready`；探活失败且找不到 exe → `unavailable`）之上补齐启动分支。下面是**完整的 `ensure_ready` 与两个内部辅助函数**，直接替换 T1 里的 `ensure_ready` 占位版本：

在 T1 已有的 `ensure_ready()` 占位实现（探活成功 → `ready`；探活失败且找不到 exe → `unavailable`）之上，补齐启动/拉模型/并发控制。下面是**完整的 `ensure_ready` 与两个内部辅助函数**，直接替换 T1 里的 `ensure_ready`：

```python
# ── T2 追加：启动流程锁 ──
# _start_lock：串行化「探活→启动→拉模型」，避免与 _state_lock（只保护状态读写）
# 混用导致 status() 阻塞
_start_lock = threading.Lock()


def _terminate_owned() -> None:
    """关闭本应用启动的 Ollama；外部实例（managed=False）一律不碰。

    锁契约：调用方**不得**持有 `_state_lock`（threading.Lock 不可重入）。
    本函数自行分两段——锁内「摘账」（快照 proc、清空 _proc/_managed，
    防止其他线程重入同一进程），锁外做进程操作（terminate → 等宽限期
    → 超时才 kill），避免长时间阻塞 `status()`。

    终止时序用 POSIX 常规的「terminate → wait(timeout) → 超时才 kill」，
    **不做无条件盲睡**：Windows 上 TerminateProcess 即刻生效，盲睡 5s 纯属
    浪费（且拖住并发的 ensure_ready、给每次进程退出加 5s）。
    """
    global _managed, _proc
    with _state_lock:
        proc = _proc
        if proc is None or not _managed:
            return
        _proc = None
        _managed = False
    try:
        proc.terminate()
        try:
            proc.wait(timeout=TERMINATE_GRACE)   # 顺带回收子进程句柄，避免 GC 期 "subprocess still running"
        except subprocess.TimeoutExpired:
            # 宽限期内没退出（信号被忽略/进程卡在内核态）→ 强杀
            proc.kill()
            try:
                proc.wait(timeout=TERMINATE_GRACE)
            except Exception as exc:  # noqa: BLE001 - 兜底失败也不抛
                logger.warning("ollama wait after kill failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 - 关闭失败不抛
        logger.warning("ollama terminate failed: %s", exc)
        try:
            proc.kill()  # terminate 抛错时兜底，避免摘账后进程失管
        except Exception as kill_exc:  # noqa: BLE001 - 兜底失败也不抛
            logger.warning("ollama kill failed: %s", kill_exc)


def _wait_ready(host: str) -> bool:
    """轮询探活直到就绪或超过 START_TIMEOUT。"""
    deadline = _monotonic() + START_TIMEOUT
    while _monotonic() < deadline:
        if _probe(host, PROBE_TIMEOUT) is not None:
            return True
        _sleep(POLL_INTERVAL)
    return False


def _has_model(tags: dict[str, Any] | None) -> bool:
    """tags 里是否已有所需 embedding 模型（裸名视为同一模型的 tag 变体）。"""
    if not isinstance(tags, dict):
        return False
    models = tags.get("models")
    if not isinstance(models, list):
        return False
    want = _model()
    bare = want.split(":")[0]
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model") or ""
        if name == want or str(name).split(":")[0] == bare:
            return True
    return False


def _ensure_model(
    host: str, exe: str, flags: int, tags: dict[str, Any] | None = None
) -> bool:
    """确保所需 embedding 模型已就位；缺失则 `ollama pull`。

    返回是否具备模型。pull 的**退出码必须校验**——模型不存在等场景
    `ollama pull` 只写 stderr 并 exit=1、不抛异常，不查 rc 会误标 ready。
    失败只转 failed 状态 + 日志（硬约束 2），不抛给调用方。

    `tags` 用于复用调用方**刚探到**的 /api/tags 结果：managed 分支上一步
    探活已经拿到同一份数据，再探一次纯属多一次 HTTP 往返（模型列表不小）。
    传 None（默认）表示没现成结果，需要自己探。
    """
    global _state, _detail
    if tags is None:
        tags = _probe(host, PROBE_TIMEOUT)
    if _has_model(tags):
        return True
    with _state_lock:
        _state, _detail = "downloading", f"正在拉取模型 {_model()}"
    logger.info("pulling embedding model %s (first run may take minutes)", _model())
    try:
        rc = _spawn([exe, "pull", _model()], flags).wait()
    except Exception as exc:  # noqa: BLE001 - 拉取失败不抛给对话层
        with _state_lock:
            _state, _detail = "failed", f"拉取模型失败: {exc}"
        logger.warning("ollama pull failed: %s", exc)
        return False
    if rc != 0:
        with _state_lock:
            _state, _detail = "failed", f"拉取模型失败（退出码 {rc}）"
        logger.warning("ollama pull exited with code %s", rc)
        return False
    return True


def ensure_ready() -> None:
    """确保 Ollama 可用：探活 → 按需启动 → 按需拉模型。

    幂等；并发调用只启动一个进程；任何失败都不抛异常，只转状态与日志。
    """
    global _state, _detail, _managed, _proc, _last_probe_ok   # _last_used 归 touch() 所有

    # 全流程串行化；状态字段只短持 _state_lock（见代码块后的「锁分工」说明）
    with _start_lock:
        now = _monotonic()
        # 探活节流：5 秒内已成功探活且状态正常则不重复发 HTTP
        with _state_lock:
            throttled = _state == "ready" and (now - _last_probe_ok) < THROTTLE_WINDOW
        if throttled:
            touch()
            return

        host = _host()
        tags = _probe(host, PROBE_TIMEOUT)
        if tags is not None:
            if _managed:
                # 我们自己启动的进程：上次 pull 可能失败导致模型仍缺，补拉重试；
                # managed=False 的外部实例一律不 pull（不改用户环境）
                exe = _find_exe()
                if exe is not None:
                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    # 复用上面探活拿到的 tags，少一次 GET /api/tags
                    if not _ensure_model(host, exe, flags, tags=tags):
                        return  # _ensure_model 已置 failed，不得覆盖成 ready
                else:
                    # 进程在跑但找不到 exe → 无法确认模型是否就位；不能静默标
                    # ready（模型仍缺却报可用，用户会在下游才炸）
                    with _state_lock:
                        _state, _detail = "failed", "未找到 ollama 可执行文件，无法确认模型"
                    logger.warning("ollama executable not found; cannot verify embedding model")
                    return
            with _state_lock:
                first_sight = not _managed and _state != "ready"
                _state, _detail = "ready", ""
                _last_probe_ok = now
            if first_sight:
                # 首次探测到外部实例：明确记日志，后续一律不接管不关闭
                logger.info("ollama already running at %s, leaving it untouched", host)
            touch()
            # 恢复路径（上次 pull 失败、本次探活成功）也必须起回收线程：
            # 本次请求没 spawn 过进程，若只挂在 spawn 分支末尾就会静默失去空闲回收
            if _managed:
                _ensure_reaper()      # T3 定义；T2 阶段可先留空实现占位
            return

        # 探活失败：先看是不是我们启动的进程仍在启动中
        # （poll 是 OS 系统调用，快照后放锁外执行，_state_lock 只做毫秒级保护）
        with _state_lock:
            managed_snap, proc_snap = _managed, _proc
        starting = managed_snap and proc_snap is not None and proc_snap.poll() is None
        if starting:
            if _wait_ready(host):
                with _state_lock:
                    _state, _detail = "ready", ""
                    _last_probe_ok = _monotonic()
                touch()
                if _managed:      # 该分支必然 managed（starting 的前提），门只为与 spawn 分支语义一致
                    _ensure_reaper()      # T3 定义；T2 阶段可先留空实现占位
            else:
                _terminate_owned()          # 锁外调用：函数自行锁内摘账
                with _state_lock:
                    _state, _detail = "failed", f"启动超时（{int(START_TIMEOUT)}s）"
                logger.warning("ollama start timed out after %ss", int(START_TIMEOUT))
            return

        # autostart 门：探活失败（外部实例不在）且非启动中才判——设计 §5
        # 「探活 → 失败后才判 autostart」，避免误报「已关闭」掩盖可用实例
        if not _autostart():
            with _state_lock:
                _state, _detail = "unavailable", "自动唤起已关闭（OLLAMA_AUTOSTART=0）"
            return

        exe = _find_exe()
        if exe is None:
            with _state_lock:
                _state, _detail = "unavailable", "未找到 ollama 可执行文件"
            logger.warning("ollama executable not found; auto-start disabled")
            return

        with _state_lock:
            _state, _detail = "starting", ""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = _spawn([exe, "serve"], flags)
        except Exception as exc:  # noqa: BLE001 - 启动失败不抛给对话层
            with _state_lock:
                _proc = None
                _managed = False
                _state, _detail = "unavailable", f"启动失败: {exc}"
            logger.warning("ollama start failed: %s", exc)
            return
        with _state_lock:
            _proc = proc
            _managed = True
        logger.info("ollama started (managed), pid=%s", getattr(proc, "pid", "?"))

        if not _wait_ready(host):
            _terminate_owned()              # 锁外调用：函数自行锁内摘账
            with _state_lock:
                _state, _detail = "failed", f"启动超时（{int(START_TIMEOUT)}s）"
            logger.warning("ollama start timed out after %ss", int(START_TIMEOUT))
            return

        if not _ensure_model(host, exe, flags):
            return  # _ensure_model 已置 failed，不得覆盖成 ready
        with _state_lock:
            _state, _detail = "ready", ""
            _last_probe_ok = _monotonic()
        touch()
        logger.info("ollama ready (managed)")
        _ensure_reaper()      # T3 定义；T2 阶段可先留空实现占位
```

> 锁分工：`_start_lock` 串行化启动流程（可长达 30s），`_state_lock` 只保护状态字段的读写（毫秒级），`_used_lock` 只保护 `_last_used`（只由 `touch()` 写）。**任何阻塞操作（`_wait_ready` 的 30s 轮询、`ollama pull`、`_terminate_owned` 的宽限期）都必须在 `_state_lock` 之外**——`_terminate_owned` 自带锁内摘账，调用时严禁已持 `_state_lock`（threading.Lock 不可重入，会死锁）。

> **`_ensure_reaper()` 前向引用**：它由 T3 定义。按顺序执行 T2 时请先在文件末尾加空实现占位（`def _ensure_reaper() -> None: ...`），T3 会替换成真正的惰性建线程实现。回收线程**不能只挂在 spawn 分支末尾**——「上次 pull 失败、本次探活成功」的恢复路径不 spawn 进程，漏挂会让该用户静默失去空闲回收。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/unit/test_ollama_runtime.py -v`

预期：13 个用例（7 + 6）全部 PASS / SKIPPED，无 FAIL。ruff：`ruff check src/knowledge/ollama_runtime.py tests/unit/test_ollama_runtime.py` → `All checks passed!`

- [ ] **步骤 5：Commit**

```powershell
git add src/knowledge/ollama_runtime.py tests/unit/test_ollama_runtime.py
git commit -m "feat: Ollama 按需启动、模型拉取与并发去重"
```

### 任务 T3：活跃标记、空闲回收与进程退出钩子

**文件：**
- 修改：`src/knowledge/ollama_runtime.py`（新增 `touch`/`maybe_reap`/`_reaper_loop`/`_ensure_reaper`/`shutdown_if_managed` + `atexit` 注册）
- 修改：`tests/unit/test_ollama_runtime.py`（追加 7 个用例）

- [ ] **步骤 1：编写失败的测试**

追加到 `tests/unit/test_ollama_runtime.py` 末尾：

```python
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
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/unit/test_ollama_runtime.py -v`

预期：T1+T2 的 18 个仍 PASS（或 1 skipped），新增用例 FAIL，报 `AttributeError: module 'src.knowledge.ollama_runtime' has no attribute 'touch'`（`maybe_reap`/`shutdown_if_managed`/`_ensure_reaper` 同样缺失）。约 `10 failed, 18 passed`。

- [ ] **步骤 3：编写最少实现代码**

在 `src/knowledge/ollama_runtime.py` 末尾追加（`_terminate_owned` 已由 T2 提供，此处直接复用）：

```python
def touch() -> None:
    """标记最近一次 embedding 使用，供空闲回收判定。"""
    global _last_used
    with _used_lock:
        _last_used = _monotonic()


def maybe_reap() -> bool:
    """空闲超过阈值且进程由本应用启动 → 关闭它。返回是否执行了关闭。

    回收门是 `_state == "ready"`：failed 说明模型缺失/拉取失败等，detail 是
    sidebar 上唯一的诊断信息，此时回收会把它洗成空，还让注定失败的 pull 按
    请求反复付冷启动。starting/downloading 全程在 `_start_lock` 内、回收线程
    根本看不到它们，所以 gate ready 即精确。

    锁纪律：先取 `_start_lock` 再判定/关闭——避免在 `ensure_ready` 的
    spawn/`_wait_ready`/`_ensure_model`（几分钟的 pull）进行中把进程回收掉；
    判定与状态写入各自短持 `_state_lock`；`_terminate_owned()` 必须在
    `_state_lock` 锁外调用（它自行锁内摘账，而 threading.Lock 不可重入）。

    最承重的不变量：`_managed = True` **只在持有 `_start_lock` 的启动路径里
    被赋值**（进程账目只有启动流程会写），所以拿到 `_start_lock` 之后不可能
    正处在起进程的过程中——这就是「不会误杀启动中进程」的根本依据。
    """
    global _state, _detail
    with _start_lock:
        with _used_lock:
            idle = _monotonic() - _last_used
        limit = _idle_seconds()
        if limit <= 0 or idle < limit:
            return False
        with _state_lock:
            # 硬约束 1：只关自己启动的进程，外部实例（用户自己起的）一律放过
            if not _managed or _state != "ready":
                return False
            _state, _detail = "idle", f"空闲 {int(idle // 60)} 分钟，准备关闭"
        logger.info("ollama idle for %.0f min, shutting down (managed process)", idle / 60)
        _terminate_owned()      # 仅要求不持 _state_lock；_start_lock 下调用安全
        with _state_lock:
            # detail 保留关闭原因：sidebar 上要能看到「为什么停了」
            _state, _detail = "stopped", f"空闲 {int(idle // 60)} 分钟，已关闭"
        return True


def _reaper_loop() -> None:
    """回收线程：每 REAP_INTERVAL 秒判一次空闲（Event 等待，可被 stop 打断）。"""
    while not _reaper_stop.wait(REAP_INTERVAL):
        try:
            maybe_reap()
        except Exception as exc:  # noqa: BLE001 - 回收线程永不退出
            logger.warning("ollama reaper failed: %s", exc)


def _ensure_reaper() -> None:
    """惰性创建回收线程（只创建一次；阈值为 0 时不建）。"""
    global _reaper_started
    with _state_lock:
        if _reaper_started or _idle_seconds() <= 0:
            return
        _reaper_started = True
    _reaper_stop.clear()      # 上一轮 reset_for_tests 可能置位过，先复位再起线程
    thread = threading.Thread(target=_reaper_loop, name="ollama-reaper", daemon=True)
    try:
        thread.start()
    except RuntimeError:
        # 起线程失败必须回滚，否则空闲回收永久静默失效且无任何报错
        with _state_lock:
            _reaper_started = False
        logger.warning("ollama reaper thread failed to start", exc_info=True)


def shutdown_if_managed() -> None:
    """进程退出钩子：只终止本应用启动的 Ollama（锁外调用，函数自行摘账）。

    刻意**不取** `_start_lock`：退出时进程正在启动/拉模型就意味着不能马上杀，
    等它反而更糟（退出钩子会把整个解释器挂住直到对方释放锁）。此时抢锁外
    terminate 是正确取舍——最坏情况是刚起的进程被立刻关掉，下次提问重启。
    """
    _terminate_owned()


atexit.register(shutdown_if_managed)
```

并在 `ensure_ready()` 的**三条 ready 路径末尾**（`touch()` 之后）各调一次 `_ensure_reaper()`，让「本次真的 spawn 了」和「恢复路径（上次 pull 失败、本次探活成功）」都能拿到回收线程——只挂 spawn 分支会让后者静默失去空闲回收：

```python
        logger.info("ollama ready (managed)")     # spawn 分支
        _ensure_reaper()
        # 探活成功（恢复）分支 / starting 分支同样在 touch() 后写：
        #     if _managed:
        #         _ensure_reaper()
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/unit/test_ollama_runtime.py -v --durations=5`

预期：33 个用例全部 PASS（或 1 skipped），无 FAIL；最慢用例 < 1s（回收/启动互斥的两个用例各有一次故意 0.2s 的阻塞断言，**不得出现 30s 级耗时**）。ruff：`ruff check src/knowledge/ollama_runtime.py tests/unit/test_ollama_runtime.py` → `All checks passed!`

- [ ] **步骤 5：Commit**

```powershell
git add src/knowledge/ollama_runtime.py tests/unit/test_ollama_runtime.py
git commit -m "feat: Ollama 空闲回收、活跃标记与进程退出钩子"
```

### 任务 T4：embedding 包裹层 + 配置项

> ⚠️ **`status()` 保持三键**（`state`/`managed`/`detail`，被 T1 测试与设计 §6 锁定）。若本任务或 sidebar 需要「空闲剩余时间」，请在 `ollama_runtime` 新增**独立访问器**（如 `idle_for()`），不要往 `status()` 里加第 4 个键。

**文件：**
- 修改：`src/knowledge/llm_factory.py:51-77`（`build_embedding_func` 内加 async 包裹层）
- 修改：`src/config.py:102-103`（新增两个键）
- 修改：`.env.example:39-40`（注释说明）
- 测试：`tests/unit/test_llm_factory.py`（追加 7 个用例）、`tests/unit/test_ollama_runtime.py`（追加 2 个配置读取用例）

- [ ] **步骤 1：编写失败的测试**

追加到 `tests/unit/test_llm_factory.py` 末尾（7 个用例）：

```python
def test_build_embedding_func_returns_async_wrapper():
    """包裹层必须是 async（lightrag 的 ollama_embed.func 实测为 async）。"""
    import inspect

    func = build_embedding_func().func
    assert inspect.iscoroutinefunction(func)


def test_wrapper_calls_ensure_ready_and_touch(monkeypatch):
    """每次 embedding 调用前先 ensure_ready + touch。"""
    from src.knowledge import ollama_runtime

    calls: list[str] = []
    monkeypatch.setattr(ollama_runtime, "ensure_ready", lambda: calls.append("ensure"))
    monkeypatch.setattr(ollama_runtime, "touch", lambda: calls.append("touch"))

    build_embedding_func().func(["hello"], max_token_size=8192)

    assert calls == ["ensure", "touch"]


def test_wrapper_delegates_to_ollama_embed(monkeypatch):
    """包裹层必须把 texts 原样委托给原始实现并返回其结果。"""
    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    seen: dict = {}

    async def fake_inner(texts, **kwargs):
        seen["texts"] = texts
        seen["kwargs"] = kwargs
        return [[0.1, 0.2]]

    monkeypatch.setattr(ollama_runtime, "ensure_ready", lambda: None)
    monkeypatch.setattr(ollama_runtime, "touch", lambda: None)
    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    result = build_embedding_func().func(["hello"])

    assert seen["texts"] == ["hello"]
    assert seen["kwargs"]["embed_model"] == "bge-m3:latest"
    assert result == [[0.1, 0.2]]


def test_wrapper_preserves_model_name(monkeypatch):
    """model_name 不变 → LightRAG 的向量库隔离/建库锁定语义零变化。"""
    from src import config

    monkeypatch.setattr(config, "EMBEDDING_MODEL", "bge-m3")
    assert build_embedding_func().model_name == "bge-m3:latest"


def test_custom_embedding_func_is_not_wrapped():
    """传入自定义 embedding_func 时原样返回，不被 Ollama 包裹。"""

    def custom(texts, **kwargs):
        return [[0.0]]

    assert build_embedding_func(custom) is custom


def test_wrapper_failure_of_ensure_does_not_break_call(monkeypatch):
    """ensure_ready 抛异常时仍继续委托原始实现（不把异常带给 LightRAG）。"""
    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    def boom():
        raise RuntimeError("state file corrupted")

    async def fake_inner(texts, **kwargs):
        return [[1.0]]

    monkeypatch.setattr(ollama_runtime, "ensure_ready", boom)
    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    assert build_embedding_func().func(["x"]) == [[1.0]]


def test_wrapper_keeps_event_loop_alive_while_ensure_blocks(monkeypatch):
    """ensure_ready 阻塞时事件循环不被卡死（必须 to_thread，不能同步直调）。

    实测依据：ensure_ready 最坏持 _start_lock 30s+；若同步直调，
    事件循环线程被占满，scenario 内的 asyncio.sleep 永远排不上，
    总耗时 ≥ release 超时（5s）→ 断言 <2s 失败（红）。
    """
    import asyncio
    import threading as th
    import time as time_mod

    from lightrag.llm.ollama import ollama_embed

    from src.knowledge import ollama_runtime

    release = th.Event()

    def blocking_ensure():
        release.wait(timeout=5)  # 模拟最坏 30s 的启动/拉模型持锁

    async def fake_inner(texts, **kwargs):
        return [[0.0]]

    monkeypatch.setattr(ollama_runtime, "ensure_ready", blocking_ensure)
    monkeypatch.setattr(ollama_runtime, "touch", lambda: None)
    monkeypatch.setattr(ollama_embed, "func", fake_inner)

    async def scenario():
        task = asyncio.ensure_future(build_embedding_func().func(["x"]))
        await asyncio.sleep(0.05)  # 同步直调时这行根本排不上（loop 线程被占）
        release.set()              # 走到这里才放行阻塞的 ensure
        return await task

    start = time_mod.monotonic()
    assert asyncio.run(scenario()) == [[0.0]]
    assert time_mod.monotonic() - start < 2  # to_thread：<0.1s；同步直调：≥5s
```

**新建 `tests/unit/test_ollama_config.py`**（2 个用例）。这两个用例必须独立成文件：`test_ollama_runtime.py` 的 `clean_runtime` 夹具会把 `_autostart`/`_idle_seconds` 替换成假函数，在该文件里断言配置读取会被夹具屏蔽：

```python
"""配置读取用例：不受 test_ollama_runtime 的 clean_runtime 夹具替换影响。"""
from __future__ import annotations

from src import config
from src.knowledge import ollama_runtime as rt


def test_autostart_reads_config_flag(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_AUTOSTART", False)
    assert rt._autostart() is False
    monkeypatch.setattr(config, "OLLAMA_AUTOSTART", True)
    assert rt._autostart() is True


def test_idle_seconds_reads_config_minutes(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 3)
    assert rt._idle_seconds() == 180.0
    monkeypatch.setattr(config, "OLLAMA_IDLE_MINUTES", 0)
    assert rt._idle_seconds() == 0.0
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/unit/test_llm_factory.py tests/unit/test_ollama_config.py -v`

预期：`test_autostart_reads_config_flag` / `test_idle_seconds_reads_config_minutes` FAIL，报 `AttributeError: module 'src.config' has no attribute 'OLLAMA_AUTOSTART'`；`test_wrapper_delegates_to_ollama_embed` FAIL（`seen["kwargs"]["embed_model"]` KeyError，因为 T1 的 `ensure_ready` 占位实现还没接入包裹层——`build_embedding_func` 尚未加包裹）。约 `7 failed`，既有 `test_llm_factory` 老用例保持 PASS。

- [ ] **步骤 3：编写最少实现代码**

`src/config.py` 第 102-103 行后追加：

```python
# 本地 Ollama（embedding 用）
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
# 自动唤起：embedding 首次被使用时若 Ollama 未运行则拉起 ollama serve
OLLAMA_AUTOSTART = os.environ.get("OLLAMA_AUTOSTART", "1").strip().lower() not in (
    "0", "false", "no", "off", "",
)
# 自动关闭：空闲超过该分钟数后关闭「本应用启动的」Ollama（<=0 表示不自动关闭）
try:
    OLLAMA_IDLE_MINUTES = int(os.environ.get("OLLAMA_IDLE_MINUTES", "10"))
except ValueError:
    # 手误的环境变量不该让整个应用起不来：回退默认并留痕
    OLLAMA_IDLE_MINUTES = 10
```

`.env.example` 第 39-40 行（`# OLLAMA_URL=` / `# EMBEDDING_MODEL=`）后追加：

```
# 自动唤起：embedding 首次使用时若 Ollama 未运行则自动拉起（1=开，0=关）
# OLLAMA_AUTOSTART=1
# 自动关闭：空闲超过该分钟数后关闭「本应用启动的」Ollama（<=0 表示不自动关闭）
# OLLAMA_IDLE_MINUTES=10
```

`src/knowledge/llm_factory.py` 顶部补 `import asyncio`（当前只有 `import logging`），`build_embedding_func` 末尾（`return replace(...)` 之前）替换为：

```python
    # Ollama 裸名等价 :latest 标签：补 tag 后与历史硬编码默认值
    # bge-m3:latest 完全一致（env 未设或设为 bge-m3 时行为零变化）
    model = EMBEDDING_MODEL if ":" in EMBEDDING_MODEL else f"{EMBEDDING_MODEL}:latest"
    # .func 取未包装的原始函数，用 partial 绑定 embed_model 使模型名真正生效
    inner = partial(ollama_embed.func, embed_model=model)

    async def _guarded(texts, **kwargs):
        """按需唤起 Ollama 后委托原始实现。

        透明性保证：签名与返回值形状不变、不抛新异常（ensure 失败也继续委托），
        因此对 LightRAG 与 KGMemory 两个调用方零感知。
        """
        from src.knowledge import ollama_runtime

        try:
            # ensure_ready 最坏持 _start_lock 30s+（启动轮询，pull 全程更久），
            # 必须 to_thread 卸载到工作线程——同步直调会冻结整个事件循环
            # （连 /api/sidebar 的请求一起卡死）
            await asyncio.to_thread(ollama_runtime.ensure_ready)
            ollama_runtime.touch()  # 毫秒级时间戳写入，保持同步即可
        except Exception as exc:  # noqa: BLE001 - 保活失败不应影响 embedding
            logger.warning("ollama ensure failed: %s", exc)
        return await inner(texts, **kwargs)

    return replace(ollama_embed, func=_guarded, model_name=model)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/unit/test_llm_factory.py tests/unit/test_ollama_config.py tests/unit/test_ollama_runtime.py -v`

预期：全部 PASS（`test_ollama_runtime` 25 + `test_ollama_config` 2 + `test_llm_factory` 既有 + 新增 7）。ruff：`ruff check src/knowledge/llm_factory.py src/config.py tests/unit/test_llm_factory.py tests/unit/test_ollama_config.py` → `All checks passed!`

- [ ] **步骤 5：Commit**

```powershell
git add src/knowledge/llm_factory.py src/config.py .env.example tests/unit/test_llm_factory.py tests/unit/test_ollama_config.py
git commit -m "feat: embedding 调用前自动唤起 Ollama 并新增保活配置"
```

### 任务 T5：侧边栏暴露 Ollama 状态

**文件：**
- 修改：`src/api/routes_meta.py:35-63`（`_env_status` 返回值新增 `ollama`）
- 修改：`tests/unit/test_api_meta.py`（追加 2 个用例）

- [ ] **步骤 1：编写失败的测试**

追加到 `tests/unit/test_api_meta.py` 末尾：

```python
def test_sidebar_env_includes_ollama_status(client):
    """env.ollama 三个字段必须存在，供前端展示保活状态。"""
    body = client.get("/api/sidebar").json()

    ollama = body["env"]["ollama"]
    assert set(ollama) == {"state", "managed", "detail"}
    assert isinstance(ollama["state"], str)
    assert isinstance(ollama["managed"], bool)
    assert isinstance(ollama["detail"], str)


def test_sidebar_env_ollama_degrades_when_status_raises(client, monkeypatch):
    """status() 异常时降级为 error 标记，绝不让 /api/sidebar 500。"""
    from src.knowledge import ollama_runtime

    def boom():
        raise RuntimeError("state file corrupted")

    monkeypatch.setattr(ollama_runtime, "status", boom)

    response = client.get("/api/sidebar")

    assert response.status_code == 200
    ollama = response.json()["env"]["ollama"]
    assert ollama["state"] == "error"
    assert ollama["managed"] is False
    assert "corrupted" in ollama["detail"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/unit/test_api_meta.py -v`

预期：新增 2 个 FAIL——`KeyError: 'ollama'`（`_env_status` 尚未返回该键）。既有 11 个 sidebar 用例保持 PASS。

- [ ] **步骤 3：编写最少实现代码**

`src/api/routes_meta.py` 的 `_env_status` 函数体末尾，把

```python
    return {"rscript": rscript, "kb_path": kb_path, "kb_ok": kb_ok}
```

替换为

```python
    from src.knowledge import ollama_runtime

    try:
        ollama = ollama_runtime.status()
    except Exception as exc:  # noqa: BLE001 - 自检失败不阻断
        logger.warning("ollama status failed: %s", exc)
        ollama = {"state": "error", "managed": False, "detail": str(exc)}
    return {"rscript": rscript, "kb_path": kb_path, "kb_ok": kb_ok, "ollama": ollama}
```

并把该函数 docstring 的第二段改为：

```python
    四段探测各自 try/except：r_executor 缺失/config 缺失/which 失败
    一律降级成响应里的标记（rscript=None、kb_path 默认值），绝不 500。
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/unit/test_api_meta.py -v`

预期：13 个用例全部 PASS。ruff：`ruff check src/api/routes_meta.py tests/unit/test_api_meta.py` → `All checks passed!`

- [ ] **步骤 5：Commit**

```powershell
git add src/api/routes_meta.py tests/unit/test_api_meta.py
git commit -m "feat: 侧边栏暴露 Ollama 保活状态"
```

### 任务 T6：全量门禁与真机验收

**文件：**
- 验证：全仓（不改代码，验收发现问题时回到 T1–T5 修）

- [ ] **步骤 1：运行全量后端门禁**

运行：`python -m pytest tests/ -q`

预期：`499 passed, 7 skipped`（基线 469 + 新增 30）。

- [ ] **步骤 2：运行 ruff 门禁**

运行：

```powershell
ruff check src/knowledge/ollama_runtime.py src/knowledge/llm_factory.py src/config.py src/api/routes_meta.py tests/unit/test_ollama_runtime.py tests/unit/test_ollama_config.py tests/unit/test_llm_factory.py tests/unit/test_api_meta.py
```

预期：`All checks passed!`（本任务只涉及这些文件；`ruff check src/ tests/` 的预存告警不在范围内）。

- [ ] **步骤 3：确认自动唤起生效（真机，Ollama 处于停止状态）**

先确认 Ollama 未运行：`Invoke-RestMethod http://127.0.0.1:11434/api/tags` 应连接失败（失败即符合预期）。

再在**同一个进程**里跑一次真实 embedding 并读状态（状态是进程内模块变量，不能跨进程查）：

```powershell
python -c "import asyncio, logging; logging.basicConfig(level=logging.INFO); from src.knowledge.llm_factory import build_embedding_func; from src.knowledge import ollama_runtime; v = asyncio.run(build_embedding_func().func(['hello'])); print('vector_len=', len(v[0])); print(ollama_runtime.status())"
```

预期：打印 `vector_len=1024`（bge-m3 维度）与 `{'state': 'ready', 'managed': True, 'detail': ''}`；日志出现 `ollama started (managed), pid=...` 与 `ollama ready (managed)`。**不再出现** `Failed to connect to Ollama`。

- [ ] **步骤 4：确认空闲后自动关闭（同一进程内观察）**

```powershell
$env:OLLAMA_IDLE_MINUTES = "1"
python -c "import asyncio, logging, time; logging.basicConfig(level=logging.INFO); from src.knowledge.llm_factory import build_embedding_func; from src.knowledge import ollama_runtime; asyncio.run(build_embedding_func().func(['hello'])); print('after_start=', ollama_runtime.status()); time.sleep(75); print('after_idle=', ollama_runtime.status())"
Remove-Item Env:OLLAMA_IDLE_MINUTES
```

预期：`after_start= {'state': 'ready', 'managed': True, ...}`；75 秒后 `after_idle= {'state': 'stopped', 'managed': False, 'detail': ''}`；日志出现 `ollama idle for 1 min, shutting down (managed process)`。随后 `Invoke-RestMethod http://127.0.0.1:11434/api/tags` 应连接失败。

- [ ] **步骤 5：确认不关闭用户自己启动的 Ollama（关键验收）**

```powershell
Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
```

等就绪（`Invoke-RestMethod http://127.0.0.1:11434/api/tags` 能返回）后，在同一进程内观察：

```powershell
$env:OLLAMA_IDLE_MINUTES = "1"
python -c "import asyncio, logging, time; logging.basicConfig(level=logging.INFO); from src.knowledge.llm_factory import build_embedding_func; from src.knowledge import ollama_runtime; asyncio.run(build_embedding_func().func(['hello'])); print('managed=', ollama_runtime.status()['managed']); time.sleep(75); print('still_alive=', bool(__import__('urllib.request', fromlist=['urlopen']).urlopen('http://127.0.0.1:11434/api/tags', timeout=3)))"
Remove-Item Env:OLLAMA_IDLE_MINUTES
```

预期：`managed= False`（外部实例不被接管）；日志出现 `ollama already running at http://127.0.0.1:11434, leaving it untouched`；75 秒后 `still_alive= True`（该进程未被关闭）。测完手动结束该 Ollama 进程。

- [ ] **步骤 6：Commit（若有收尾改动）**

```powershell
git status --short
```

预期：干净。若步骤 3–5 发现问题并回到 T1–T5 修复，则按修复任务提交；否则无需提交。

## 范围外（后续）

- 云端 embedding provider、provider 运行时切换、索引目录隔离（前序讨论明确砍掉，不在本计划）
- 前端渲染 `env.ollama` 字段（属计划 2 的 Sidebar 任务）
- 自动安装 Ollama（只做可执行文件探测，找不到即降级 `unavailable`）
- embedding 并发/批处理调优、失败重试




