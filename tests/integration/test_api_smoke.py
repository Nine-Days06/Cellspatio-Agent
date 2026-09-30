"""API 层全链路冒烟：建会话 → SSE 对话 → 确认 → 恢复 → 删除；main() 启动参数。"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeAgent, FakeRuntime


class NullKB:
    """侧边栏知识库桩：返回空统计，避免 /api/sidebar 走异常降级分支。"""

    def get_statistics(self) -> dict:
        return {}


@pytest.fixture
def agent() -> FakeAgent:
    instance = FakeAgent()
    instance.knowledge_client = NullKB()
    return instance


@pytest.fixture
def client(agent: FakeAgent) -> TestClient:
    return TestClient(create_app(agent))


def _sse(client: TestClient, session_id: str, prompt: str) -> list[dict]:
    response = client.post("/api/chat", json={"session_id": session_id, "prompt": prompt})
    assert response.status_code == 200
    return [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_full_flow_session_chat_confirm_restore_delete(client, agent):
    # 1. 建会话
    sid = client.post("/api/sessions", json={"title": "肝癌项目"}).json()["session"]["id"]

    # 2. 首轮：脚本确认（HITL）
    agent.agent_runtime = FakeRuntime(default={
        "status": "needs_script_confirmation",
        "message": "已生成 R 脚本",
        "script": "library(DESeq2)\nres <- DESeq(dds)",
        "analysis_type": "differential_expression",
        "params": {"group_by": "condition"},
        "method_context": None,
    })
    events = _sse(client, sid, "对这份数据做差异表达")
    assert [e["type"] for e in events] == ["confirm_card", "done"]
    assert events[0]["kind"] == "script"
    assert events[0]["payload"]["status"] == "pending"
    assert events[0]["payload"]["user_request"] == "对这份数据做差异表达"

    # 3. 确认执行
    agent.confirm_result = {"status": "success", "message": "差异分析完成", "results": None}
    confirm = client.post("/api/confirm", json={"session_id": sid, "action": "script_confirm"})
    assert confirm.status_code == 200
    assert "差异分析完成" in confirm.json()["message"]["content"]

    # 4. 第二轮：普通回答 + 图表
    import plotly.graph_objects as go

    agent.agent_runtime = FakeRuntime(
        events=[{"type": "tool_status", "name": "query_knowledge", "phase": "start"}],
        default={"status": "success", "message": "完成",
                 "results": {"charts": [go.Figure(go.Scatter(x=[1, 2], y=[3, 4]))]}},
    )
    events = _sse(client, sid, "再解释一下结果")
    assert [e["type"] for e in events] == ["tool_status", "chart", "done"]
    assert events[0]["label"] == "查询知识库"
    assert "data" in events[1]["plotly_json"]

    # 5. 历史恢复（不带 restore：pending 已确认，不该被改写）
    body = client.get(f"/api/sessions/{sid}/messages").json()
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "assistant", "user", "assistant"]
    assert body["messages"][-1]["results"]["charts"][0]["type"] == "plotly"
    assert body["pending_script"] is None
    assert body["soft_warn"] is False
    assert client.get("/api/sidebar").json()["kb_stats"] == {}

    # 6. 删除会话后资源不可达
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/sessions/{sid}/messages").status_code == 404
    assert client.post("/api/chat", json={"session_id": sid, "prompt": "hi"}).status_code == 404


def test_restore_expires_pending_after_restart(client, agent):
    """刷新页面 = 重新拉历史：pending 卡片按 expired 语义返回。"""
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    agent.agent_runtime = FakeRuntime(default={
        "status": "needs_script_confirmation",
        "message": "已生成 R 脚本",
        "script": "library(DESeq2)",
        "analysis_type": "single_cell",
        "params": {},
    })
    _sse(client, sid, "做聚类")

    # 新进程等价物：全新 TestClient + 全新 agent
    fresh = TestClient(create_app(FakeAgent()))

    body = fresh.get(f"/api/sessions/{sid}/messages?restore=1").json()

    assert body["pending_script"]["status"] == "expired"
    assert body["pending_script"]["analysis_type"] == "single_cell"
    # 已过期，确认端点必须 409
    assert fresh.post("/api/confirm", json={
        "session_id": sid, "action": "script_confirm",
    }).status_code == 409


def test_data_confirm_flow_persists_asset_for_next_turn(client, agent):
    """两段式数据获取：候选卡片 → 确认下载 → 资产进入下一轮 context。"""
    sid = client.post("/api/sessions", json={}).json()["session"]["id"]
    agent.agent_runtime = FakeRuntime(default={
        "status": "needs_confirmation",
        "type": "fetch_data",
        "candidates": [{"source": "geo", "asset_id": "GSE1", "title": "HCC RNA-seq"}],
        "query": "肝癌",
        "message": "找到 1 个候选数据集，请选择要下载的项：",
    })
    events = _sse(client, sid, "帮我下载肝癌数据集")
    assert events[0]["kind"] == "data"
    assert events[0]["payload"]["candidates"][0]["asset_id"] == "GSE1"

    agent.download_result = {
        "status": "success",
        "asset": {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"},
    }
    response = client.post("/api/confirm", json={
        "session_id": sid, "action": "data_confirm",
        "source": "geo", "asset_id": "GSE1", "query": "肝癌",
    })
    assert response.status_code == 200

    agent.agent_runtime = FakeRuntime()
    _sse(client, sid, "对刚下载的数据做差异表达")
    context = agent.agent_runtime.calls[0]["context"]

    assert context["downloaded_assets"] == [
        {"asset_id": "GSE1", "access_path": "data/raw/geo/GSE1/matrix.gz"}
    ]
    assert SessionStore().get_session(sid)["downloaded_assets"]


def test_main_wires_agent_and_runs_uvicorn(monkeypatch):
    """main() 必须：构建真实 agent → 延迟开浏览器 → 用 127.0.0.1:8600 跑 uvicorn。

    CellSpatioAgent 与 uvicorn 都被替换，因此本测试不碰 LLM、不起真服务。
    """
    module = importlib.import_module("src.api.__main__")
    captured: dict = {}
    opened: list[str] = []

    class StubAgent:
        pass

    class StubUvicorn:
        @staticmethod
        def run(app, **kwargs):
            captured.update({"app": app, **kwargs})

    monkeypatch.setattr("src.main.CellSpatioAgent", StubAgent)
    # raising=False：步骤 2 红灯时 __main__ 还没有 uvicorn 属性，不能因此提前报错
    monkeypatch.setattr(module, "uvicorn", StubUvicorn, raising=False)
    monkeypatch.setattr(module, "_open_browser_later", lambda url: opened.append(url))

    module.main()

    assert isinstance(captured["app"], FastAPI)
    assert isinstance(captured["app"].state.agent, StubAgent)
    assert captured["host"] == module.HOST == "127.0.0.1"
    assert captured["port"] == module.PORT == 8600
    assert opened == [f"http://{module.HOST}:{module.PORT}"]
