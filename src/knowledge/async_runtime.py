"""进程级共享事件循环运行时：为所有 PG 后端 LightRAG 提供唯一的事件循环。

为什么必须「进程级共享」而非「每实例一个 loop」：
- LightRAG 的 PostgreSQL 连接池由 ClientManager 持有，是**进程级单例**
  （类变量 `_instances`）；主库/方法库/KG 记忆三个实例共用同一个池。
- asyncpg 连接池**严格绑定创建它的那个事件循环**：跨 loop 复用会抛
  `InterfaceError: cannot perform operation: another operation is in progress`
  （已实测，见 `.scratch/pg-backend-fixes`）。
- 故所有实例必须在**同一个 loop** 上初始化并操作，否则实例之间互相踩池。

实现：一个守护线程跑 `loop.run_forever()`，所有同步调用经
`asyncio.run_coroutine_threadsafe` 提交到该 loop 并等待结果。任何线程（主线程、
FastAPI 工作线程、`asyncio.to_thread` 线程）都可安全调用。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class AsyncRuntime:
    """进程级单例：专用守护线程上跑一个长活事件循环。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """惰性创建 loop 与驱动线程（首次调用时注册 atexit 关闭）。"""
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_forever,
                args=(loop,),
                name="lightrag-async-runtime",
                daemon=True,
            )
            thread.start()
            self._loop, self._thread = loop, thread
            return loop

    @staticmethod
    def _run_forever(loop: asyncio.AbstractEventLoop) -> None:
        """线程入口：把 loop 设为该线程当前 loop 并常驻运行。"""
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """把协程提交到共享 loop 执行并返回结果（阻塞当前线程）。

        Args:
            coro: 待执行的协程。

        Returns:
            协程的返回值。

        Raises:
            RuntimeError: 在共享 loop 线程内调用（无法自阻塞），或 loop 已关闭。
        """
        loop = self._ensure_loop()
        if threading.current_thread() is self._thread:
            coro.close()
            raise RuntimeError("不能在共享运行时线程内调用同步接口；请改用 LightRAG 的 a* 异步方法")
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
            raise
        return future.result()

    def shutdown(self) -> None:
        """停止 loop 线程并关闭 loop：先取消 pending 任务，再关闭，幂等。"""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
        if loop is None:
            return
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)
        if loop.is_closed():
            return
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def register_atexit(fn) -> None:
    """注册 atexit 清理并抑制退出期 logging 报错。

    解释器退出时 stdout 可能已关闭（如 pytest 拆除捕获流），此时存储 finalize
    内部的 logger.info 会抛 "I/O operation on closed file" 并打印错误栈刷屏。
    退出期关闭 logging.raiseExceptions 是消除该噪声的标准做法。
    """

    def _quiet() -> None:
        prev = logging.raiseExceptions
        logging.raiseExceptions = False
        try:
            fn()
        finally:
            logging.raiseExceptions = prev

    _quiet.atexit_target = fn
    atexit.register(_quiet)


runtime = AsyncRuntime()
"""模块级单例：全进程共用，导入即得。"""

# 最早注册 → atexit LIFO 下最后执行：各 LightRAGClient.close() 先 finalize 存储
# （关 PG 池）时才用得到这个 loop，故 shutdown 必须晚于它们
register_atexit(runtime.shutdown)
