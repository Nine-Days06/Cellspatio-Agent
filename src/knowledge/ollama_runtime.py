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
                _ensure_reaper()
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
                    _ensure_reaper()
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
        _ensure_reaper()          # 自启的进程才可能需要空闲回收，外部实例不建线程


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
