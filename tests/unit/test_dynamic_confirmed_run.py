"""公开确认入口将真实执行产物接回持久化的准备运行。"""

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.analysis.r_executor import RExecutorError
from src.api.app import create_app
from src.api.chat_service import run_turn
from src.main import CellSpatioAgent
from src.ui.confirm_flow import ConfirmFlowError, confirm_script
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeExec, write_declared_output
from tests.unit.test_dynamic_pending_run import make_agent


def prepare_card(
    root: Path, analysis_type: str
) -> tuple[SessionStore, str, dict[str, Any], SimpleNamespace]:
    """经过真实 chat_service、SQLite 和 WRROC 准备一张卡。"""
    store = SessionStore(root / "sessions.db")
    sid = store.create_session("确认动态分析")
    source = root / "counts.csv"
    source.write_text("gene,A,B\nG1,1,2\n", encoding="utf-8")
    store.replace_local_asset(sid, {"source": "local", "access_path": str(source)})
    agent, _ = make_agent(root, store, analysis_type)
    result = run_turn(agent, store, sid, "分析数据", lambda event: True)
    return store, sid, result, agent


def confirmed_app(root: Path, store: SessionStore, analysis_type: str) -> tuple[Any, Any]:
    """重建应用，不调用 GET restore；确认请求必须自行恢复。"""
    agent, _ = make_agent(root, store, analysis_type)
    agent.execute_confirmed_script = CellSpatioAgent.execute_confirmed_script.__get__(agent)
    app = create_app(agent)
    app.state.store_factory = lambda: SessionStore(root / "sessions.db")
    return app, agent


def test_nested_preparation_params_are_copied_and_confirmed_after_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """准备事实与调用方嵌套参数隔离，落盘卡刷新后按完整绑定确认原运行。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, original = prepare_card(tmp_path, "differential_expression")
    prepared["params"]["options"] = {"groups": ["control", "treated"], "threshold": 0.05}
    recorder = original.workflow_manager.workflow_recorder
    recorder.save_pending_script(prepared["run_id"], prepared)
    pending = store.get_messages(sid)[-1]["pending_script"]
    pending["params"] = prepared["params"]
    store.append_message(sid, "assistant", "带嵌套参数的准备卡", pending_script=pending)
    prepared["params"]["options"]["groups"][0] = "changed"
    expected = {"groups": ["control", "treated"], "threshold": 0.05}
    assert (
        recorder.get_record(prepared["run_id"]).meta["dynamic_script"]["params"]["options"]
        == expected
    )

    app, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(app) as client:
        restored = client.get(f"/api/sessions/{sid}/messages?restore=1")
        assert restored.status_code == 200
        assert restored.json()["pending_script"]["status"] == "pending"
        assert restored.json()["pending_script"]["params"]["options"] == expected
        assert rebuilt.workflow_manager.r_executor.codes == []
        response = client.post("/api/confirm", json={"session_id": sid, "action": "script_confirm"})
    assert response.status_code == 200
    assert response.json()["message"]["content"].startswith("分析完成: ")
    assert len(rebuilt.workflow_manager.r_executor.codes) == 1
    facts = rebuilt.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    assert facts.meta["dynamic_script"]["params"]["options"] == expected


@pytest.mark.parametrize("analysis_type", ["differential_expression", "spatial"])
def test_confirm_after_reconstruction_records_repaired_outputs_on_prepared_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, analysis_type: str
) -> None:
    """准备身份、时间与步骤保留；结果、空间图和实际修复脚本归同一运行。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    # Python 3.10 没有 file_digest；公开确认路径仍须登记真实产物。
    monkeypatch.delattr(hashlib, "file_digest", raising=False)
    monkeypatch.setenv("CELLSPATIO_SESSIONS_DB", str(tmp_path / "sessions.db"))
    store, sid, prepared, original = prepare_card(tmp_path, analysis_type)
    before = original.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    app, rebuilt = confirmed_app(tmp_path, store, analysis_type)
    fixed = prepared["script"] + "\n# 实际修复脚本\n"
    attempts: list[str] = []

    def execute(code: str) -> SimpleNamespace:
        """只替代 R 边界，按实际脚本写出真实文件。"""
        attempts.append(code)
        if len(attempts) == 1:
            raise RExecutorError("需要修复")
        write_declared_output(code)
        if analysis_type == "spatial":
            Path(prepared["params"]["plot_file"]).write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/p8AAAAASUVORK5CYII="
                )
            )
        return SimpleNamespace(returncode=0)

    rebuilt.workflow_manager.r_executor = FakeExec(side_effect=execute)
    rebuilt.workflow_manager.code_repairer = SimpleNamespace(repair=lambda code, error: fixed)
    with TestClient(app) as client:
        response = client.post("/api/confirm", json={"session_id": sid, "action": "script_confirm"})
        duplicate = client.post(
            "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
        )
    assert response.status_code == 200
    assert duplicate.status_code == 409
    assert attempts == [prepared["script"], fixed]
    recorder = rebuilt.workflow_manager.workflow_recorder
    after = recorder.restore_run(prepared["run_id"])
    assert after.run_id == before.run_id
    assert after.timestamp == before.timestamp
    assert after.meta["context"] == before.meta["context"]
    assert after.steps[: len(before.steps)] == before.steps
    assert after.meta["dynamic_script"]["script"] == prepared["script"]
    assert after.meta["dynamic_script"]["status"] == "success"
    assert len(recorder.store.list_runs()) == 1
    outputs = {output.path: output for output in after.outputs}
    assert prepared["params"]["output_file"] in outputs
    if analysis_type == "spatial":
        assert prepared["params"]["plot_file"] in outputs
    for output in after.outputs:
        if Path(output.path).is_file():
            assert (
                output.meta["sha256"] == hashlib.sha256(Path(output.path).read_bytes()).hexdigest()
            )
    capsule = next(Path(output.path) for output in after.outputs if output.name == "capsule")
    assert {
        str(capsule / name) for name in ("question.txt", "script.R", "intent.json", "meta.json")
    } <= set(outputs)
    assert (capsule / "script.R").read_text(encoding="utf-8") == fixed
    assert (
        json.loads((capsule / "meta.json").read_text(encoding="utf-8"))["results"]["run_id"]
        == before.run_id
    )
    execution_steps = [step for step in after.steps if step.tool == "Rscript"]
    assert [step.params["script"] for step in execution_steps] == attempts
    assert [step.output.status.value for step in execution_steps] == ["error", "success"]


@pytest.mark.parametrize(
    "mismatch", ["unknown_run", "session", "script", "params", "method", "analysis_type"]
)
def test_confirmation_rejects_mismatched_preparation_without_executing_r(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    """确认请求拒绝错配且不改原准备事实；无效卡不开放再次执行。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, original = prepare_card(tmp_path, "differential_expression")
    recorder = original.workflow_manager.workflow_recorder
    before = recorder.store.load(prepared["run_id"])
    pending = store.get_messages(sid)[-1]["pending_script"].copy()
    if mismatch == "unknown_run":
        pending["run_id"] = "run-unknown"
    elif mismatch == "session":
        sid = store.create_session("另一会话")
    elif mismatch == "script":
        pending["script"] += "\n# 被替换"
    elif mismatch == "params":
        pending["params"] = {**pending["params"], "question": "被替换"}
    elif mismatch == "method":
        pending["method_context"] = "被替换"
    else:
        pending["analysis_type"] = "spatial"
    store.append_message(sid, "assistant", "错配卡", pending_script=pending)
    app, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(app) as client:
        response = client.post("/api/confirm", json={"session_id": sid, "action": "script_confirm"})
    assert response.status_code == 200
    assert "分析完成" not in response.json()["message"]["content"]
    assert rebuilt.workflow_manager.r_executor.codes == []
    assert recorder.store.load(prepared["run_id"]) == before
    assert store.get_messages(sid)[-2]["pending_script"]["status"] == "confirmed"


def test_copied_card_cannot_execute_an_already_successful_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAS之外还校验运行终态；新造的pending卡也不能重复R。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, _ = prepare_card(tmp_path, "differential_expression")
    pending = store.get_messages(sid)[-1]["pending_script"].copy()
    app, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
            ).status_code
            == 200
        )
    record = rebuilt.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    store.append_message(sid, "assistant", "复制卡", pending_script=pending)
    second_app, second = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(second_app) as client:
        response = client.post("/api/confirm", json={"session_id": sid, "action": "script_confirm"})
    assert response.status_code == 200
    assert second.workflow_manager.r_executor.codes == []
    assert second.workflow_manager.workflow_recorder.restore_run(prepared["run_id"]) == record
    assert store.get_messages(sid)[-2]["pending_script"]["status"] == "confirmed"


@pytest.mark.parametrize("failure", ["record_replace", "kg", "message"])
def test_post_execution_failure_keeps_claim_and_prevents_implicit_reexecution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """系统边界失败不能把成功执行卡重新变成pending。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, _ = prepare_card(tmp_path, "differential_expression")
    _, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    message_id = store.get_messages(sid)[-1]["id"]
    executor = FakeExec(track_codes=True)

    def execute(code: str) -> Any:
        """真正输出落盘后注入文件系统或外部KG边界故障。"""
        result = executor.execute_code(code)
        if failure == "record_replace":
            import os

            replace = os.replace

            def reject_record(source: str, destination: str) -> None:
                """只拒绝本临时WRROC原子替换。"""
                if Path(destination) == tmp_path / ".wrroc" / prepared["run_id"] / "workflow.json":
                    raise OSError("记录磁盘不可写")
                replace(source, destination)

            monkeypatch.setattr(os, "replace", reject_record)
        elif failure == "message":
            import sqlite3

            with sqlite3.connect(tmp_path / "sessions.db") as connection:
                connection.execute(
                    "CREATE TRIGGER reject_new_message BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, '消息写入失败'); END"
                )
        return result

    if failure == "kg":

        class BrokenKG:
            """外部KG传输失败。"""

            def ingest(self, record: Any) -> None:
                raise RuntimeError("KG不可用")

        rebuilt.workflow_manager.workflow_recorder.kg_memory = BrokenKG()
    rebuilt.workflow_manager.r_executor = FakeExec(side_effect=execute)
    if failure == "message":
        with pytest.raises(Exception, match="消息写入失败"):
            confirm_script(rebuilt, store, sid, "script_confirm", message_id=message_id)
    else:
        confirm_script(rebuilt, store, sid, "script_confirm", message_id=message_id)
    with pytest.raises(ConfirmFlowError):
        confirm_script(rebuilt, store, sid, "script_confirm", message_id=message_id)
    assert executor.codes == [prepared["script"]]
    card = next(message for message in store.get_messages(sid) if message["id"] == message_id)
    assert card["pending_script"]["status"] == "confirmed"
    persisted = rebuilt.workflow_manager.workflow_recorder.store.load(prepared["run_id"])
    assert persisted.meta["dynamic_script"]["status"] == (
        "running" if failure == "record_replace" else "success"
    )
