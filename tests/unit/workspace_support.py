"""测试辅助：把请求固定到 legacy 工作区，保留旧单机测试的数据可见性。"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import VISITOR_COOKIE, hash_token
from src.ui.session_store import LEGACY_WORKSPACE_ID, SessionStore


def authenticate_legacy(client: TestClient) -> SessionStore:
    store = SessionStore()
    token = secrets.token_urlsafe(32)
    store.create_access_token(
        hash_token(token),
        "visitor",
        LEGACY_WORKSPACE_ID,
        LEGACY_WORKSPACE_ID,
        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    client.cookies.set(VISITOR_COOKIE, token)
    return store


@contextmanager
def legacy_client(app: FastAPI, **kwargs) -> Iterator[TestClient]:
    """启动 TestClient 并把请求绑定到 legacy 工作区。"""
    with TestClient(app, **kwargs) as client:
        authenticate_legacy(client)
        yield client
