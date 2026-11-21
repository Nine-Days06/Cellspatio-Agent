"""进程内登录限速：滑动窗口计数，进程重启即清零。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

_MAX_KEYS = 1024


class LoginLimiter:
    """按 key 记录时间窗口内的登录尝试，超过阈值后拒绝且不再追加。"""

    def __init__(
        self,
        window: float = 300.0,
        max_attempts: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """保存限速参数与独立计数器。"""
        self._window = window
        self._max_attempts = max_attempts
        self._clock = clock
        self._attempts: dict[str, list[float]] = {}
        self._guard = threading.Lock()

    def blocked(self, key: str) -> bool:
        """记录一次尝试；达到阈值时返回 True 且不追加时间戳。"""
        now = self._clock()
        with self._guard:
            # shortcut: 超过 1024 个 key 时清理过期项，避免随机用户名撑大内存。
            if len(self._attempts) >= _MAX_KEYS:
                stale = [
                    k
                    for k, timestamps in self._attempts.items()
                    if not any(now - t < self._window for t in timestamps)
                ]
                for k in stale:
                    self._attempts.pop(k, None)
            if len(self._attempts) >= _MAX_KEYS:
                oldest = min(self._attempts, key=lambda k: self._attempts[k][-1])
                self._attempts.pop(oldest, None)
            hits = [t for t in self._attempts.get(key, ()) if now - t < self._window]
            if len(hits) >= self._max_attempts:
                self._attempts[key] = hits
                return True
            hits.append(now)
            self._attempts[key] = hits
            return False

    def clear(self, key: str) -> None:
        """登录成功后清空该 key 的失败记录。"""
        with self._guard:
            self._attempts.pop(key, None)
