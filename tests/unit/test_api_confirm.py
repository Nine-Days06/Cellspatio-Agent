"""POST /api/confirm：脚本确认 / 取消 / 数据下载确认三条流转。"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.chat_service import session_lock
from src.ui.confirm_flow import (
    ERR_MISSING_SOURCE,
    ERR_NO_PENDING_SCRIPT,
    ERR_NOT_SCRIPT_ACTION,
    ERR_UNKNOWN_SOURCE,
    ConfirmFlowError,
    confirm_data,
    confirm_script,
)
from src.ui.confirm_protocol import (
    ACTION_DATA_CONFIRM,
    ACTION_SCRIPT_CANCEL,
    ACTION_SCRIPT_CONFIRM,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_EXPIRED,
    STATUS_PENDING,
)
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent


@pytest.fixture
def client():
    return TestClient(create_app(FakeAgent()))


def _new_session(client) -> str:
    return client.post("/api/sessions", json={}).json()["session"]["id"]


def _with_pending_script(client, status: str = STATUS_PENDING) -> str:
    sid = _new_session(client)
    SessionStore().append_message(
        sid,
        "user",
        "做差异表达",
    )
    SessionStore().append_message(
        sid,
        "assistant",
        "```r\nlibrary(DESeq2)\n```",
        pending_script={
            "script": "library(DESeq2)",
            "analysis_type": "differential_expression",
            "params": {"group_by": "condition"},
            "method_context": "DESeq2 常规流程",
            "user_request": "做差异表达",
            "status": status,
        },
    )
    return sid


def _latest_pending(session_id: str):
    return [m for m in SessionStore().get_messages(session_id) if m["pending_script"]][-1]


def test_script_confirm_runs_script_and_persists_result(client):
    sid = _with_pending_script(client)
    client.app.state.agent.confirm_result = {
        "status": "success",
        "message": "分析完成",
        "results": None,
    }

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_SCRIPT_CONFIRM,
        },
    )

    assert response.status_code == 200
    assert client.app.state.agent.confirm_calls == [
        {
            "analysis_type": "differential_expression",
            "params": {"group_by": "condition"},
            "script": "library(DESeq2)",
            "method_context": "DESeq2 常规流程",
        }
    ]
    assert _latest_pending(sid)["pending_script"]["status"] == STATUS_CONFIRMED
    stored = SessionStore().get_messages(sid)
    assert stored[-1]["role"] == "assistant"
    assert "分析完成" in stored[-1]["content"]
    assert response.json()["message"]["id"] == stored[-1]["id"]


def test_script_confirm_without_pending_returns_409(client):
    sid = _new_session(client)

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_SCRIPT_CONFIRM,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "没有待确认的脚本"


def test_script_confirm_targets_message_id_not_latest(client):
    """多标签页：A 页的旧卡不能执行 B 页新产生的脚本。"""
    sid = _with_pending_script(client)
    old_id = _latest_pending(sid)["id"]
    SessionStore().append_message(
        sid,
        "assistant",
        "```r\nnew_script()\n```",
        pending_script={
            "script": "new_script()",
            "analysis_type": "differential_expression",
            "params": {},
            "method_context": None,
            "user_request": "再跑一次",
            "status": STATUS_PENDING,
        },
    )

    response = client.post(
        "/api/confirm",
        json={"session_id": sid, "action": ACTION_SCRIPT_CONFIRM, "message_id": old_id},
    )

    assert response.status_code == 200
    assert [c["script"] for c in client.app.state.agent.confirm_calls] == ["library(DESeq2)"]
    # 新卡仍是 pending，旧卡已确认
    statuses = {
        m["id"]: m["pending_script"]["status"]
        for m in SessionStore().get_messages(sid)
        if m["pending_script"]
    }
    assert statuses[old_id] == STATUS_CONFIRMED
    assert statuses[max(statuses)] == STATUS_PENDING


def test_script_confirm_with_stale_message_id_returns_409(client):
    """卡片已被其它标签页确认后，旧 id 不能重复执行。"""
    sid = _with_pending_script(client)
    card_id = _latest_pending(sid)["id"]
    first = client.post(
        "/api/confirm",
        json={"session_id": sid, "action": ACTION_SCRIPT_CONFIRM, "message_id": card_id},
    )
    second = client.post(
        "/api/confirm",
        json={"session_id": sid, "action": ACTION_SCRIPT_CONFIRM, "message_id": card_id},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(client.app.state.agent.confirm_calls) == 1


def test_script_confirm_failure_releases_card(client):
    """执行失败要把卡片退回 pending，否则用户既看不到结果也无法重试。"""
    sid = _with_pending_script(client)
    card_id = _latest_pending(sid)["id"]

    def _boom(*args, **kwargs):
        raise RuntimeError("R 环境不可用")

    client.app.state.agent.execute_confirmed_script = _boom

    with pytest.raises(RuntimeError):
        confirm_script(
            client.app.state.agent,
            SessionStore(),
            sid,
            ACTION_SCRIPT_CONFIRM,
            message_id=card_id,
        )

    statuses = {
        m["id"]: m["pending_script"]["status"]
        for m in SessionStore().get_messages(sid)
        if m["pending_script"]
    }
    assert statuses[card_id] == STATUS_PENDING


def test_script_confirm_error_result_releases_card(client):
    """执行链以 error 结果报告失败时，卡片也要退回 pending 供用户重试。"""
    sid = _with_pending_script(client)
    card_id = _latest_pending(sid)["id"]
    client.app.state.agent.execute_confirmed_script = lambda *a, **k: {
        "status": "error",
        "analysis_type": "differential_expression",
        "message": "差异表达未产出结果文件: de.csv",
    }

    response = client.post(
        "/api/confirm",
        json={"session_id": sid, "action": ACTION_SCRIPT_CONFIRM, "message_id": card_id},
    )

    assert response.status_code == 200
    statuses = {
        m["id"]: m["pending_script"]["status"]
        for m in SessionStore().get_messages(sid)
        if m["pending_script"]
    }
    assert statuses[card_id] == STATUS_PENDING
    assert "未产出结果文件" in SessionStore().get_messages(sid)[-1]["content"]


def test_script_confirm_on_expired_returns_409(client):
    sid = _with_pending_script(client, status=STATUS_EXPIRED)

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_SCRIPT_CONFIRM,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "没有待确认的脚本"
    assert client.app.state.agent.confirm_calls == []


def test_script_cancel_marks_cancelled_and_persists_message(client):
    sid = _with_pending_script(client)

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_SCRIPT_CANCEL,
        },
    )

    assert response.status_code == 200
    assert _latest_pending(sid)["pending_script"]["status"] == STATUS_CANCELLED
    stored = SessionStore().get_messages(sid)
    assert stored[-1]["content"] == "已取消本次脚本执行。"
    assert client.app.state.agent.confirm_calls == []


def test_data_confirm_appends_asset_to_session(client):
    sid = _new_session(client)
    client.app.state.agent.download_result = {
        "status": "success",
        "asset": {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"},
    }

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_DATA_CONFIRM,
            "source": "geo",
            "asset_id": "GSE1",
            "query": "肝癌",
        },
    )

    assert response.status_code == 200
    assert client.app.state.agent.download_calls == [
        {"source": "geo", "asset_id": "GSE1", "query": "肝癌"}
    ]
    session = SessionStore().get_session(sid)
    assert session["downloaded_assets"] == [
        {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"}
    ]
    stored = SessionStore().get_messages(sid)
    assert stored[-1]["content"] == "已下载 GSE1 → `data/raw/geo/GSE1/matrix.gz`"


def test_data_confirm_without_asset_persists_failure_message(client):
    sid = _new_session(client)
    client.app.state.agent.download_result = {"status": "error", "message": "下载失败"}

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_DATA_CONFIRM,
            "source": "geo",
            "asset_id": "GSE1",
        },
    )

    assert response.status_code == 200
    session = SessionStore().get_session(sid)
    assert session["downloaded_assets"] == []
    assert SessionStore().get_messages(sid)[-1]["content"] == "下载失败"


def test_data_confirm_missing_asset_id_returns_400(client):
    sid = _new_session(client)

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_DATA_CONFIRM,
            "source": "geo",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "缺少 source 或 asset_id"


def test_unknown_action_returns_400(client):
    sid = _new_session(client)

    response = client.post("/api/confirm", json={"session_id": sid, "action": "script_regen"})

    assert response.status_code == 400
    assert response.json()["detail"] == "未知动作: script_regen"


def test_confirm_unknown_session_returns_404(client):
    response = client.post(
        "/api/confirm",
        json={
            "session_id": "nope",
            "action": ACTION_SCRIPT_CONFIRM,
        },
    )

    assert response.status_code == 404


def test_confirm_while_generation_running_returns_409(client):
    sid = _new_session(client)
    lock = session_lock(sid)
    assert lock.acquire(blocking=False) is True
    try:
        response = client.post(
            "/api/confirm",
            json={
                "session_id": sid,
                "action": ACTION_DATA_CONFIRM,
                "source": "geo",
                "asset_id": "GSE1",
            },
        )
    finally:
        lock.release()

    assert response.status_code == 409
    # confirm 端点在执行期间也可能是本端点自身持有锁 → 文案保持中性
    assert response.json()["detail"] == "该会话已有任务在进行"


def test_data_confirm_repeat_keeps_single_asset(client):
    """幂等：同一 (source, asset_id) 连续两次确认，资产只落库一次。"""
    sid = _new_session(client)
    client.app.state.agent.download_result = {
        "status": "success",
        "asset": {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"},
    }
    payload = {
        "session_id": sid,
        "action": ACTION_DATA_CONFIRM,
        "source": "geo",
        "asset_id": "GSE1",
        "query": "肝癌",
    }

    first = client.post("/api/confirm", json=payload)
    second = client.post("/api/confirm", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200  # 重复确认仍返回成功消息
    session = SessionStore().get_session(sid)
    assert session["downloaded_assets"] == [
        {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"}
    ]
    assert "已下载 GSE1" in SessionStore().get_messages(sid)[-1]["content"]


def test_data_confirm_unregistered_source_returns_structured_400():
    """未注册 source（KeyError）→ 4xx JSON detail，而非 Starlette 纯文本 500。"""
    client = TestClient(create_app(FakeAgent()), raise_server_exceptions=False)
    sid = _new_session(client)

    def _boom(source, asset_id, query=""):
        raise KeyError(f"未注册的数据源: {source}")

    client.app.state.agent.confirm_and_download = _boom

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_DATA_CONFIRM,
            "source": "not_registered",
            "asset_id": "X1",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"] == "未知数据源: not_registered"


def test_script_confirm_twice_second_gets_409(client):
    """脚本确认幂等锁：成功后再次 confirm → 409，执行器只被调用一次。"""
    sid = _with_pending_script(client)
    client.app.state.agent.confirm_result = {
        "status": "success",
        "message": "分析完成",
        "results": None,
    }
    payload = {"session_id": sid, "action": ACTION_SCRIPT_CONFIRM}

    first = client.post("/api/confirm", json=payload)
    second = client.post("/api/confirm", json=payload)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "没有待确认的脚本"
    assert len(client.app.state.agent.confirm_calls) == 1


def test_script_confirm_execute_failure_returns_502_keeps_pending():
    """执行器抛异常 → 502 结构化 JSON，pending 状态不得被写成 confirmed。"""
    client = TestClient(create_app(FakeAgent()), raise_server_exceptions=False)
    sid = _with_pending_script(client)

    def _boom(*args, **kwargs):
        raise RuntimeError("R 执行崩溃")

    client.app.state.agent.execute_confirmed_script = _boom

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_SCRIPT_CONFIRM,
        },
    )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")
    assert "R 执行崩溃" in response.json()["detail"]
    assert _latest_pending(sid)["pending_script"]["status"] == STATUS_PENDING


def test_script_cancel_on_expired_returns_409(client):
    """取消同样只接受 pending；过期脚本不得被改写成 cancelled。"""
    sid = _with_pending_script(client, status=STATUS_EXPIRED)

    response = client.post(
        "/api/confirm",
        json={
            "session_id": sid,
            "action": ACTION_SCRIPT_CANCEL,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "没有待确认的脚本"
    assert _latest_pending(sid)["pending_script"]["status"] == STATUS_EXPIRED


def test_state_machine_no_pending_error_code(client):
    sid = _new_session(client)

    with pytest.raises(ConfirmFlowError) as exc:
        confirm_script(client.app.state.agent, SessionStore(), sid, ACTION_SCRIPT_CONFIRM)

    assert exc.value.code == ERR_NO_PENDING_SCRIPT
    assert str(exc.value) == "没有待确认的脚本"


def test_state_machine_missing_source_error_code(client):
    sid = _new_session(client)

    with pytest.raises(ConfirmFlowError) as exc:
        confirm_data(client.app.state.agent, SessionStore(), sid, source=None, asset_id="GSE1")

    assert exc.value.code == ERR_MISSING_SOURCE
    assert str(exc.value) == "缺少 source 或 asset_id"


def test_state_machine_unknown_source_error_code(client):
    sid = _new_session(client)

    def _boom(source, asset_id, query=""):
        raise KeyError(f"未注册的数据源: {source}")

    client.app.state.agent.confirm_and_download = _boom

    with pytest.raises(ConfirmFlowError) as exc:
        confirm_data(client.app.state.agent, SessionStore(), sid, source="nope", asset_id="X1")

    assert exc.value.code == ERR_UNKNOWN_SOURCE
    assert str(exc.value) == "未知数据源: nope"


def test_state_machine_not_script_action_error_code(client):
    sid = _with_pending_script(client)

    with pytest.raises(ConfirmFlowError) as exc:
        confirm_script(client.app.state.agent, SessionStore(), sid, "bad_action")

    assert exc.value.code == ERR_NOT_SCRIPT_ACTION
    assert str(exc.value) == "不是脚本动作: bad_action"
    assert _latest_pending(sid)["pending_script"]["status"] == STATUS_PENDING


def _module_imports(relative_path: str) -> set[str]:
    path = Path(__file__).resolve().parents[2] / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_session_store_does_not_import_confirm_flow_or_fastapi():
    imports = _module_imports("src/ui/session_store.py")

    assert "src.ui.confirm_flow" not in imports
    assert not any(name == "fastapi" or name.startswith("fastapi.") for name in imports)
    assert not any(name == "src.control" or name.startswith("src.control.") for name in imports)


def test_confirm_protocol_has_no_third_party_imports():
    assert _module_imports("src/ui/confirm_protocol.py") <= {"__future__", "typing"}


def test_importing_confirm_flow_does_not_load_control_layer():
    """确认状态机导入不应拉起 control 包（agent_runtime/LightRAG）。"""
    repo = Path(__file__).resolve().parents[2]
    code = (
        "import sys, src.ui.confirm_flow; "
        "assert 'src.control' not in sys.modules, sorted(sys.modules)"
    )
    subprocess.run(
        [sys.executable, "-c", code], cwd=repo, check=True, capture_output=True, timeout=60
    )
