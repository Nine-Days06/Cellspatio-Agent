"""全局测试夹具：会话库重定向到 tmp_path、PG 存储重定向到测试库。"""

import asyncio
import os
import warnings

import pytest

# 测试专用 PG 库：与生产库 rag 隔离，测试写入不会污染真实知识库
_TEST_PG_DATABASE = "rag_test"
_PG_READY: bool | None = None
_PG_PROBE_TIMEOUT_SECONDS = 10


async def _ensure_test_database() -> None:
    """连 postgres 维护库确保测试库存在并启用 pgvector 扩展。"""
    import asyncpg

    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = int(os.environ.get("POSTGRES_PORT", "5433"))
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")

    conn = await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database="postgres",
        timeout=3,
        command_timeout=5,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", _TEST_PG_DATABASE
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{_TEST_PG_DATABASE}"')
    finally:
        await conn.close()

    conn = await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=_TEST_PG_DATABASE,
        timeout=3,
        command_timeout=5,
    )
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()


def pytest_sessionstart(session):
    """在任何测试连接建立前准备测试库；PG 不可达时快速降级。"""
    global _PG_READY
    os.environ["POSTGRES_DATABASE"] = _TEST_PG_DATABASE
    try:
        asyncio.run(asyncio.wait_for(_ensure_test_database(), timeout=_PG_PROBE_TIMEOUT_SECONDS))
    except Exception as exc:  # noqa: BLE001 - PG 不可达时不应阻断非 PG 测试
        _PG_READY = False
        warnings.warn(
            f"无法准备测试库 {_TEST_PG_DATABASE}: {exc}；"
            "标记为 pg 的测试将跳过，请先运行 .\\start_pg.ps1",
            stacklevel=1,
        )
    else:
        _PG_READY = True


def pytest_runtest_setup(item):
    """PG 不可达时跳过显式标记的用例，避免 LightRAG 连接重试卡住整套测试。"""
    if item.get_closest_marker("pg") and _PG_READY is not True:
        pytest.skip("PostgreSQL 未就绪；运行 .\\start_pg.ps1 后重试")


@pytest.fixture(autouse=True)
def session_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
