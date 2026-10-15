"""全局测试夹具：会话库重定向到 tmp_path、PG 存储重定向到测试库。"""

import asyncio
import os
import warnings

import pytest

# 测试专用 PG 库：与生产库 rag 隔离，测试写入不会污染真实知识库
_TEST_PG_DATABASE = "rag_test"


async def _ensure_test_database() -> None:
    """连 postgres 维护库确保测试库存在并启用 pgvector 扩展。"""
    import asyncpg

    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = int(os.environ.get("POSTGRES_PORT", "5433"))
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")

    conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database="postgres"
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
        host=host, port=port, user=user, password=password, database=_TEST_PG_DATABASE
    )
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def pg_test_database():
    """把所有 PG 存储重定向到测试库，确保生产库 rag 不被测试写入。

    LightRAG 的 ClientManager 是进程级单例：首个连接落哪个库，整个测试进程
    都复用它。故必须在任何连接建立前覆盖 POSTGRES_DATABASE（session 级
    autouse，先于所有测试执行）。建库失败时仅告警——碰 PG 的测试自然失败，
    但绝不回退到生产库。
    """
    os.environ["POSTGRES_DATABASE"] = _TEST_PG_DATABASE
    try:
        asyncio.run(_ensure_test_database())
    except Exception as exc:  # noqa: BLE001 - PG 不可达时不应阻断非 PG 测试
        warnings.warn(
            f"无法准备测试库 {_TEST_PG_DATABASE}: {exc}；涉及 PG 的测试将失败",
            stacklevel=1,
        )
    yield


@pytest.fixture(autouse=True)
def session_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
