"""全局测试夹具：会话库重定向到 tmp_path，避免污染 .cellspatio/。"""

import pytest


@pytest.fixture(autouse=True)
def session_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
