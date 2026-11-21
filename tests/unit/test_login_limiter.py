"""登录限速实例：窗口、隔离、容量清理与默认路由行为。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.public_api.app import create_public_app
from src.public_api.login_limiter import LoginLimiter


class FakeClock:
    """可手动推进的单调时钟。"""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_window_expires_old_attempts():
    """窗口内第 5 次后阻止，第 6 次不追加；窗口过期后重新放行。"""
    clock = FakeClock()
    limiter = LoginLimiter(window=10.0, max_attempts=5, clock=clock)

    for _ in range(5):
        assert not limiter.blocked("key")
    assert limiter.blocked("key")
    assert limiter.blocked("key")

    clock.now = 10.0
    assert not limiter.blocked("key")

    for _ in range(4):
        limiter.blocked("cleared")
    limiter.clear("cleared")
    assert not limiter.blocked("cleared")


def test_blocked_does_not_append_attempt():
    """拒绝后的重复请求不追加时间戳，旧记录到期即可重新尝试。"""
    clock = FakeClock()
    limiter = LoginLimiter(window=10.0, max_attempts=1, clock=clock)
    assert not limiter.blocked("key")
    clock.now = 5.0
    assert limiter.blocked("key")
    clock.now = 10.0
    assert not limiter.blocked("key")


def test_instances_are_isolated(tmp_path):
    """两个限速器与两个 app 实例互不共享计数。"""
    first = LoginLimiter(max_attempts=1, clock=FakeClock())
    second = LoginLimiter(max_attempts=1, clock=FakeClock())
    assert not first.blocked("key")
    assert first.blocked("key")
    assert not second.blocked("key")

    first_client = TestClient(create_public_app(tmp_path / "first"))
    second_client = TestClient(create_public_app(tmp_path / "second"))
    for _ in range(5):
        assert (
            first_client.post(
                "/api/auth/login", json={"username": "isolated", "password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        first_client.post(
            "/api/auth/login", json={"username": "isolated", "password": "wrong"}
        ).status_code
        == 429
    )
    assert (
        second_client.post(
            "/api/auth/login", json={"username": "isolated", "password": "wrong"}
        ).status_code
        == 401
    )


def test_capacity_cleanup_keeps_active_keys():
    """超过 1024 个 key 时只清过期项，活跃 key 的计数保留。"""
    clock = FakeClock()
    limiter = LoginLimiter(window=10.0, clock=clock)
    for index in range(1024):
        limiter.blocked(f"stale-{index}")
    limiter.blocked("active")

    clock.now = 5.0
    limiter.blocked("active")
    clock.now = 14.0
    limiter.blocked("trigger")
    for _ in range(4):
        assert not limiter.blocked("active")
    assert limiter.blocked("active")


def test_capacity_is_bounded_even_with_all_active_keys():
    """随机用户名持续涌入时，字典不能无限增长。"""
    limiter = LoginLimiter(window=300.0, clock=FakeClock())
    for index in range(5000):
        limiter.blocked(f"user-{index}")
    assert len(limiter._attempts) <= 1024


def test_login_route_uses_limiter_from_app_state(tmp_path):
    """路由读取 app.state 的实例，默认窗口为 300 秒。"""
    app = create_public_app(tmp_path)
    clock = FakeClock()
    app.state.login_limiter = LoginLimiter(clock=clock)
    client = TestClient(app)

    for _ in range(5):
        assert (
            client.post(
                "/api/auth/login", json={"username": "clocked", "password": "wrong"}
            ).status_code
            == 401
        )
    clock.now = 299.0
    assert (
        client.post(
            "/api/auth/login", json={"username": "clocked", "password": "wrong"}
        ).status_code
        == 429
    )
    clock.now = 300.0
    assert (
        client.post(
            "/api/auth/login", json={"username": "clocked", "password": "wrong"}
        ).status_code
        == 401
    )


def test_login_returns_429_after_threshold(tmp_path):
    """HTTP 登录连续失败 5 次后返回 429，默认配置不变。"""
    client = TestClient(create_public_app(tmp_path))
    for _ in range(5):
        response = client.post(
            "/api/auth/login", json={"username": "limiter-user", "password": "wrong"}
        )
        assert response.status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "limiter-user", "password": "wrong"})
    assert blocked.status_code == 429
