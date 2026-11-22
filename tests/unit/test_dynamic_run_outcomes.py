"""公开确认入口保留取消、失败和兼容运行的真实事实。"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.analysis.r_executor import RExecutorError
from src.main import CellSpatioAgent
from tests.unit.fakes import FakeExec
from tests.unit.test_dynamic_confirmed_run import confirmed_app, prepare_card


def test_cancel_records_original_run_without_executing_r(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨请求取消绑定原准备身份，保存取消事实而非完成执行。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, original = prepare_card(tmp_path, "differential_expression")
    before = original.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    app, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    rebuilt.cancel_pending_script = lambda *args, **kwargs: CellSpatioAgent.cancel_pending_script(
        rebuilt, *args, **kwargs
    )
    with TestClient(app) as client:
        response = client.post("/api/confirm", json={"session_id": sid, "action": "script_cancel"})
    assert response.status_code == 200
    assert "已取消" in response.json()["message"]["content"]
    after = rebuilt.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    assert after.timestamp == before.timestamp
    assert after.steps[: len(before.steps)] == before.steps
    assert after.meta["dynamic_script"]["status"] == "cancelled"
    assert after.steps[-1].output.result["status"] == "cancelled"
    assert after.outputs == []
    assert rebuilt.workflow_manager.r_executor.codes == []


def test_cancel_rejects_mismatched_original_identity_without_claiming_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消同样在公开入口核验绑定；篡改卡不会取消原运行。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, original = prepare_card(tmp_path, "differential_expression")
    before = original.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    changed = store.get_messages(sid)[-1]["pending_script"].copy()
    changed["script"] += "\n# changed"
    store.append_message(sid, "assistant", "错配卡", pending_script=changed)
    app, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    rebuilt.cancel_pending_script = lambda *args, **kwargs: CellSpatioAgent.cancel_pending_script(
        rebuilt, *args, **kwargs
    )
    with TestClient(app) as client:
        response = client.post("/api/confirm", json={"session_id": sid, "action": "script_cancel"})
    assert response.status_code == 200
    assert "不匹配" in response.json()["message"]["content"]
    assert "已取消" not in response.json()["message"]["content"]
    assert rebuilt.workflow_manager.workflow_recorder.restore_run(prepared["run_id"]) == before
    assert rebuilt.workflow_manager.r_executor.codes == []


def test_known_failure_survives_reconstruction_and_explicit_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已知失败如实保存；只读恢复不执行，明确重试留存原失败步骤。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, _ = prepare_card(tmp_path, "differential_expression")
    app, first = confirmed_app(tmp_path, store, "differential_expression")

    def fail(code: str) -> None:
        """仅替代外部 R 执行失败。"""
        raise RExecutorError("真实执行失败")

    first.workflow_manager.r_executor = FakeExec(side_effect=fail)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
            ).status_code
            == 200
        )
    failed = first.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    assert failed.meta["dynamic_script"]["status"] == "failed"
    assert failed.steps[-1].output.status.value == "error"
    assert failed.steps[-1].output.result["status"] == "error"
    assert store.get_messages(sid)[-2]["pending_script"]["status"] == "pending"
    second_app, second = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(second_app) as client:
        restored = client.get(f"/api/sessions/{sid}/messages?restore=1")
        assert restored.json()["pending_script"]["run_id"] == prepared["run_id"]
        assert second.workflow_manager.r_executor.codes == []
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
            ).status_code
            == 200
        )
    after = second.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    assert after.meta["dynamic_script"]["status"] == "success"
    assert after.steps[: len(failed.steps)] == failed.steps
    assert len(second.workflow_manager.r_executor.codes) == 1
    assert len({step.step_id for step in after.steps}) == len(after.steps)


@pytest.mark.parametrize("fault_kind", ["write", "read"])
def test_cancel_disk_failure_releases_only_own_claim_for_explicit_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault_kind: str
) -> None:
    """真实存储 IO 故障不留下虚假取消；明确再取消可保存，旧令牌不能回滚新取消。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, _ = prepare_card(tmp_path, "differential_expression")
    app, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    rebuilt.cancel_pending_script = lambda *args, **kwargs: CellSpatioAgent.cancel_pending_script(
        rebuilt, *args, **kwargs
    )
    tokens = []
    workflow_file = tmp_path / ".wrroc" / prepared["run_id"] / "workflow.json"
    read_text = Path.read_text

    def fail_replace(source: str, target: str) -> None:
        """仅在真实 WRROC 原子替换 IO 边界注入磁盘错误。"""
        tokens.append(store.get_messages(sid)[-1]["pending_script"].get("claim_token", ""))
        raise OSError("取消落盘失败")

    def fail_read(path: Path, *args: Any, **kwargs: Any) -> str:
        """只对该 WRROC 文件注入读取错误，其他真实文件 IO 继续运行。"""
        if path == workflow_file:
            fail_replace(str(path), str(path))
        return read_text(path, *args, **kwargs)

    with TestClient(app, raise_server_exceptions=False) as client:
        with monkeypatch.context() as fault:
            if fault_kind == "write":
                fault.setattr("src.control.wrroc_store.os.replace", fail_replace)
            else:
                fault.setattr(Path, "read_text", fail_read)
            assert (
                client.post(
                    "/api/confirm", json={"session_id": sid, "action": "script_cancel"}
                ).status_code
                == 502
            )
        assert store.get_messages(sid)[-1]["pending_script"]["status"] == "pending"
        assert (
            rebuilt.workflow_manager.workflow_recorder.restore_run(prepared["run_id"]).meta[
                "dynamic_script"
            ]["status"]
            == "pending"
        )
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_cancel"}
            ).status_code
            == 200
        )
    card = store.get_messages(sid)[-2]
    assert tokens[0]
    store.release_script(sid, card["id"], tokens[0], claimed_status="cancelled")
    assert store.get_messages(sid)[-2]["pending_script"]["status"] == "cancelled"
    assert (
        rebuilt.workflow_manager.workflow_recorder.restore_run(prepared["run_id"]).meta[
            "dynamic_script"
        ]["status"]
        == "cancelled"
    )
    assert rebuilt.workflow_manager.r_executor.codes == []


def test_legacy_card_creates_explicit_compatibility_run_without_guessing_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无ID卡只记录本次兼容执行，不把旧准备记录冒充为已找回的身份。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, original = prepare_card(tmp_path, "differential_expression")
    old = store.get_messages(sid)[-1]["pending_script"].copy()
    old.pop("run_id")
    store.append_message(sid, "assistant", "旧卡", pending_script=old)
    app, rebuilt = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
            ).status_code
            == 200
        )
    recorder = rebuilt.workflow_manager.workflow_recorder
    assert len(recorder.store.list_runs()) == 2
    compatibility = next(
        recorder.restore_run(run.run_id)
        for run in recorder.store.list_runs()
        if run.run_id != prepared["run_id"]
    )
    assert compatibility.meta["confirmation_origin"] == "legacy_without_run_id"
    assert compatibility.meta["dynamic_script"]["status"] == "success"
    assert compatibility.outputs
    assert (
        original.workflow_manager.workflow_recorder.restore_run(prepared["run_id"]).meta[
            "dynamic_script"
        ]["status"]
        == "pending"
    )
    assert store.get_messages(sid)[-2]["pending_script"].get("run_id") is None


def test_unknown_execution_interruption_remains_nonretryable_even_with_copied_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无法证实执行结果时记录中断；重建、恢复和复制卡都不能隐式重跑。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store, sid, prepared, _ = prepare_card(tmp_path, "differential_expression")
    pending = store.get_messages(sid)[-1]["pending_script"].copy()
    app, first = confirmed_app(tmp_path, store, "differential_expression")

    def interrupt(code: str) -> None:
        """外部执行器中断，不声称知道 R 的终态。"""
        raise RuntimeError("执行结果未知")

    first.workflow_manager.r_executor = FakeExec(side_effect=interrupt)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
            ).status_code
            == 200
        )
    interrupted = first.workflow_manager.workflow_recorder.restore_run(prepared["run_id"])
    assert interrupted.meta["dynamic_script"]["status"] == "interrupted"
    assert interrupted.steps[-1].output.result["retry_allowed"] is False
    assert store.get_messages(sid)[-2]["pending_script"]["status"] == "confirmed"
    store.append_message(sid, "assistant", "复制卡", pending_script=pending)
    second_app, second = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(second_app) as client:
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
            ).status_code
            == 200
        )
    assert second.workflow_manager.r_executor.codes == []
    assert store.get_messages(sid)[-2]["pending_script"]["status"] == "confirmed"
    assert second.workflow_manager.workflow_recorder.restore_run(prepared["run_id"]) == interrupted
