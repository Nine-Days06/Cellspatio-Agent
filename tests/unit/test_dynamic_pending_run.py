"""动态脚本准备经过真实会话落盘和应用重建后仍可恢复。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.app import create_app
from src.api.chat_service import run_turn
from src.control.agent_runtime import AgentRuntime
from src.control.workflow_manager import WorkflowManager
from src.control.workflow_recorder import WorkflowRecorder
from src.schemas.workflow import WorkflowExecution
from src.ui.analysis_roots import AnalysisRoots
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeExec, FakeGen, FakeIntent
from tests.unit.workspace_support import legacy_client


class MemoryTransport:
    """记录远程 KG transport 收到的运行，不访问外部服务。"""

    def __init__(self) -> None:
        self.runs: list[WorkflowExecution] = []

    def ingest(self, record: WorkflowExecution) -> None:
        """接收完成运行。"""
        self.runs.append(record)


def make_agent(
    root: Path, store: SessionStore, analysis_type: str = "differential_expression"
) -> tuple[SimpleNamespace, MemoryTransport]:
    """保留真实 runtime/manager/recorder，只替换外部意图、生成和 R。"""
    memory = MemoryTransport()
    recorder = WorkflowRecorder(root / ".wrroc", repo_root=root, kg_memory=memory)
    manager = WorkflowManager(
        intent_parser=FakeIntent(
            parse_return={"type": "analysis", "analysis_type": analysis_type},
            extract_params_return={"input_files": []},
        ),
        knowledge_client=None,
        r_executor=FakeExec(track_codes=True),
        visualizer=None,
        r_script_generator=FakeGen(return_value="# 完整的待确认脚本\n" + "# 内容\n" * 40),
        workflow_recorder=recorder,
        require_script_confirmation=True,
        analysis_roots=AnalysisRoots(store, root),
    )
    agent = SimpleNamespace(
        workflow_manager=manager,
        agent_runtime=AgentRuntime(None, "offline", manager),
        llm_client=None,
    )
    return agent, memory


@pytest.mark.parametrize("analysis_type", ["differential_expression", "spatial"])
def test_prepared_run_survives_card_persistence_and_app_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, analysis_type: str
) -> None:
    """刷新恢复同一准备运行，完整事实可读，未执行 R 或发布完成运行。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "0")
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    store = SessionStore(tmp_path / "sessions.db")
    session_id = store.create_session("恢复动态分析")
    source = tmp_path / "counts.csv"
    source.write_text("gene,A,B\nG1,1,2\n", encoding="utf-8")
    store.replace_local_asset(session_id, {"source": "local", "access_path": str(source)})
    agent, memory = make_agent(tmp_path, store, analysis_type)
    events: list[dict] = []
    result = run_turn(agent, store, session_id, "做差异表达", lambda e: events.append(e) or True)

    assert result["status"] == "needs_script_confirmation"
    run_id = result["run_id"]
    prepared = agent.workflow_manager.workflow_recorder.get_record(run_id)
    assert agent.workflow_manager.r_executor.codes == []
    assert memory.runs == []
    assert not (tmp_path / "snapshots").exists()

    rebuilt_store = SessionStore(tmp_path / "sessions.db")
    rebuilt_agent, rebuilt_memory = make_agent(tmp_path, rebuilt_store, analysis_type)
    app = create_app(rebuilt_agent)
    app.state.store_factory = lambda: SessionStore(tmp_path / "sessions.db")
    with legacy_client(app) as client:
        response = client.get(f"/api/sessions/{session_id}/messages?restore=1")

    assert response.status_code == 200
    pending = response.json()["pending_script"]
    assert pending["run_id"] == run_id
    assert pending["status"] == "pending"
    card = rebuilt_store.get_messages(session_id)[-1]["pending_script"]
    assert card["run_id"] == run_id
    assert card["status"] == "pending"
    event = next(e for e in events if e["type"] == "confirm_card")
    assert event["payload"]["run_id"] == run_id
    restored = rebuilt_agent.workflow_manager.workflow_recorder.restore_run(run_id)
    assert restored.run_id == run_id
    assert restored.timestamp == prepared.timestamp
    facts = restored.meta["dynamic_script"]
    assert facts == {
        "status": "pending",
        "session_id": session_id,
        "analysis_type": analysis_type,
        "script": result["script"],
        "params": result["params"],
        "method_context": None,
    }
    assert len(facts["script"]) > 100
    assert [s.step_id for s in restored.steps] == [s.step_id for s in prepared.steps]
    if analysis_type == "differential_expression":
        assert [s.step_id for s in restored.steps] == [
            "de_analysis_start",
            "de_analysis_script_generated",
        ]
    assert restored.outputs == []
    assert rebuilt_agent.workflow_manager.r_executor.codes == []
    assert rebuilt_memory.runs == []


@pytest.mark.parametrize("mismatch", ["unknown_run", "session", "script", "params", "method"])
def test_restore_keeps_expiry_protection_when_preparation_cannot_be_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    """有运行号也必须核验落盘事实；错配卡保持原过期保护。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session("来源会话")
    source = tmp_path / "counts.csv"
    source.write_text("gene,A,B\nG1,1,2\n", encoding="utf-8")
    agent, memory = make_agent(tmp_path, store)
    result = agent.workflow_manager.run_analysis_for_agent(
        "differential_expression", {"input_files": [str(source)]}, {"session_id": sid}
    )
    pending = {
        k: result[k] for k in ("run_id", "script", "analysis_type", "params", "method_context")
    }
    pending.update(status="pending", user_request="做差异表达")
    if mismatch == "unknown_run":
        pending["run_id"] = "run-unknown"
    elif mismatch == "session":
        sid = store.create_session("其他会话")
    elif mismatch == "script":
        pending["script"] += "\n# 被替换"
    elif mismatch == "params":
        pending["params"] = {**pending["params"], "group_csv": "changed.csv"}
    else:
        pending["method_context"] = "不同方法"
    store.append_message(sid, "assistant", "脚本卡", pending_script=pending)
    rebuilt, _ = make_agent(tmp_path, store)
    app = create_app(rebuilt)
    app.state.store_factory = lambda: SessionStore(tmp_path / "sessions.db")
    with legacy_client(app) as client:
        response = client.get(f"/api/sessions/{sid}/messages?restore=1")
    assert response.json()["pending_script"]["status"] == "expired"
    assert response.json()["pending_script"]["run_id"] == pending["run_id"]
    assert store.get_messages(sid)[-1]["pending_script"]["status"] == "expired"
    assert agent.workflow_manager.r_executor.codes == []
    assert memory.runs == []


def test_without_recorder_preserves_legacy_pending_contract(tmp_path: Path) -> None:
    """无 recorder 不伪造可恢复运行身份。"""
    store = SessionStore(tmp_path / "sessions.db")
    agent, _ = make_agent(tmp_path, store)
    agent.workflow_manager.workflow_recorder = None
    source = tmp_path / "counts.csv"
    source.write_text("gene,A,B\nG1,1,2\n", encoding="utf-8")
    result = agent.workflow_manager.run_analysis_for_agent(
        "differential_expression", {"input_files": [str(source)]}
    )
    assert result["status"] == "needs_script_confirmation"
    assert "run_id" not in result
    assert result["params"]["input_file"] == str(source)
    assert agent.workflow_manager.r_executor.codes == []
