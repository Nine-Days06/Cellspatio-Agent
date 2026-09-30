"""Ollama 进程自动唤起与自动关闭。

embedding 唯一后端是本地 Ollama（见 llm_factory.build_embedding_func）。
本模块负责：用到时按需拉起 `ollama serve`，空闲超过阈值后关闭**自己启动的**进程。

三条硬约束：
1. 绝不启动/关闭用户自己启动的 Ollama——探活成功即视为外部实例（managed=False）。
2. 任何失败都不抛到调用方，只转成 status() 的状态 + 日志（保持对话链路行为不变）。
3. 探活与启动均幂等：并发首次调用只会启动一个进程。
"""
from __future__ import annotations

import atexit  # noqa: F401 - T3 才会调用 atexit.register(shutdown_if_managed)，提前保留 import
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
    """当前 Ollama 状态：state / managed / detail，供侧边栏展示。"""
    with _state_lock:
        return {"state": _state, "managed": _managed, "detail": _detail}


def ensure_ready() -> None:
    """占位版：探活 Ollama 并更新状态，供 T4 的 embedding 包裹层调用。

    - 探活成功 → ready（外部实例，managed=False）
    - 探活失败且找不到可执行文件 → unavailable
    - 「探活失败但有 exe → 拉起进程」的启动分支属 T2，此处保持原状态
    任何异常都不抛出（硬约束 2），只记日志。
    """
    global _state, _detail, _last_probe_ok
    try:
        if _probe(_host(), PROBE_TIMEOUT) is not None:
            with _state_lock:
                # T2 替换本函数时同样禁止在成功路径重置 _managed（设计 §5：managed 保持原值）
                _state, _detail = "ready", ""
                _last_probe_ok = _monotonic()
            return
        # 探活失败：启动分支 T2 补齐，T1 只标记「彻底不可用」
        exe = _find_exe()
        with _state_lock:
            if exe is None:
                _state, _detail = "unavailable", "ollama 服务未响应且未找到可执行文件"
    except Exception:  # 硬约束 2：失败只转状态不抛出
        logger.exception("ensure_ready 执行异常")


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
