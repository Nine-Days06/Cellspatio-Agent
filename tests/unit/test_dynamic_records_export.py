"""公开会话/项目导出消费真实动态运行产物，保留来源与路径契约。"""

import base64
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.chat_service import run_turn
from src.cli.records import export_records
from src.control.workflow_recorder import WorkflowRecorder
from src.ui.session_store import SessionStore
from tests.unit.fakes import FakeExec, write_declared_output
from tests.unit.test_dynamic_confirmed_run import confirmed_app
from tests.unit.test_dynamic_pending_run import make_agent


@pytest.mark.parametrize("scope", ["session", "project"])
def test_export_registered_dynamic_outputs_with_original_run_and_no_discovery_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    """真实空间 CSV/PNG/胶囊归原运行；旧目录补录不倒推身份，其他会话隔离。"""
    monkeypatch.setenv("CELLSPATIO_LOCAL", "1")
    store = SessionStore(tmp_path / "sessions.db")
    project = store.create_project("空间项目")
    sid = store.create_session("空间会话", project_id=project)
    source = tmp_path / "counts.csv"
    source.write_text("gene,A,B\nG1,1,2\n", encoding="utf-8")
    store.replace_local_asset(sid, {"source": "local", "access_path": str(source)})
    agent, _ = make_agent(tmp_path, store, "spatial")
    prepared = run_turn(agent, store, sid, "空间分析", lambda event: True)
    app, rebuilt = confirmed_app(tmp_path, store, "spatial")

    def execute(code: str) -> SimpleNamespace:
        """R adapter 写真实 CSV 与可解码 PNG。"""
        write_declared_output(code)
        Path(prepared["params"]["plot_file"]).write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/p8AAAAASUVORK5CYII="
            )
        )
        return SimpleNamespace(returncode=0)

    rebuilt.workflow_manager.r_executor = FakeExec(side_effect=execute)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/confirm", json={"session_id": sid, "action": "script_confirm"}
            ).status_code
            == 200
        )
    legacy = Path(prepared["params"]["output_file"]).parent / "legacy.csv"
    legacy.write_text("gene,padj\nOLD,0.05\n", encoding="utf-8")
    other = store.create_session("隔离会话")
    store.replace_local_asset(other, {"source": "local", "access_path": str(source)})
    other_agent, _ = make_agent(tmp_path, store)
    other_prepared = run_turn(other_agent, store, other, "做差异表达", lambda event: True)
    other_app, _ = confirmed_app(tmp_path, store, "differential_expression")
    with TestClient(other_app) as client:
        assert (
            client.post(
                "/api/confirm", json={"session_id": other, "action": "script_confirm"}
            ).status_code
            == 200
        )
    assert Path(other_prepared["params"]["output_file"]).is_file()
    archive_path = tmp_path / f"{scope}.zip"
    export_records(
        scope,
        sid if scope == "session" else project,
        out=archive_path,
        hash_files=True,
        store=store,
        root=tmp_path,
    )
    with zipfile.ZipFile(archive_path) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("artifacts.csv").decode("utf-8"))))
        name = "record.json" if scope == "session" else f"sessions/{sid}/record.json"
        record = json.loads(archive.read(name))
    linked = [row for row in rows if row.get("run_id") == prepared["run_id"]]
    assert len(linked) == 6
    assert len({row["path"] for row in rows}) == len(rows)
    assert all(row["session_id"] == sid and row["source"] == "wrroc_output" for row in linked)
    for row in linked:
        path = tmp_path / row["path"]
        assert path.is_file()
        assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(record["artifacts"]) == 6
    assert all(run["input"]["context"]["session_id"] == sid for run in record["workflow_runs"])
    assert all(row["run_id"] != other_prepared["run_id"] for row in rows)
    discoveries = [row for row in rows if row["path"].endswith("legacy.csv")]
    if scope == "project":
        assert len(discoveries) == 1
        assert discoveries[0]["run_id"] == ""
        assert discoveries[0]["session_id"] == ""
        assert discoveries[0]["source"] == "directory_discovery"
    else:
        assert discoveries == []


@pytest.mark.parametrize("scope", ["session", "project"])
def test_redacted_export_covers_dynamic_scripts_params_and_old_record_paths(
    tmp_path: Path, scope: str
) -> None:
    """redact=False 默认保留记录；显式脱敏覆盖新 meta 和旧字段中的绝对路径。"""
    store = SessionStore(tmp_path / "sessions.db")
    project = store.create_project("脱敏项目")
    sid = store.create_session("脱敏会话", project_id=project)
    references = [
        "https://example.org/#/paper",
        "https://example.org/foo-/bar",
        "http://example.org:8080/foo./bar?q=a%2Fb#/paper",
        "HTTPS://example.org/paper_(2026)/part?x=1&y=2#section/path",
    ]
    store.append_message(sid, "user", r"读取C:\Users\private-person\patient\counts.csv")
    store.append_message(sid, "user", "读取/home/private-person/patient/counts.csv")
    adjacent_paths = [
        r"C:\Users\private-person\patient\counts.csv",
        "/home/private-person/patient/counts.csv",
        r"\\private-server\private-share\patient\counts.csv",
    ]
    for separator in "，。；：！？、）】》”’":
        for path in adjacent_paths:
            store.append_message(sid, "user", f"资料{references[0]}{separator}输入{path}")
    store.append_message(sid, "user", "/home/private-person/counts.csv " + " ".join(references))
    workflow = tmp_path / ".wrroc" / "old-run" / "workflow.json"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "input": {
                    "context": {
                        "session_id": sid,
                        "reference_url": "https://example.org/paper",
                        "reference_urls": references,
                    }
                },
                "meta": {
                    "dynamic_script": {
                        "script": (
                            "read.csv('/srv/private sample/input.csv')\nwrite.csv(x, '/opt/private output/out.csv')\n"
                            f"# 文献 {' '.join(references)}\n"
                            r"read.csv(r'(\\private-server\private-share\patient\counts.csv)')"
                        ),
                        "params": {
                            "input_file": "/srv/private sample/input.csv",
                            "custom_path": "/opt/private output/out.csv",
                            "references": references,
                        },
                    }
                },
                "outputs": [{"path": str(tmp_path / "missing.csv")}],
            }
        ),
        encoding="utf-8",
    )
    target = sid if scope == "session" else project
    plain = tmp_path / "plain.zip"
    hidden = tmp_path / "redacted.zip"
    export_records(scope, target, out=plain, store=store, root=tmp_path)
    export_records(scope, target, out=hidden, redact=True, store=store, root=tmp_path)
    with zipfile.ZipFile(plain) as archive:
        assert any(b"private sample" in archive.read(name) for name in archive.namelist())
    with zipfile.ZipFile(hidden) as archive:
        record_name = "record.json" if scope == "session" else f"sessions/{sid}/record.json"
        assert "https://example.org/paper" in archive.read(record_name).decode("utf-8")
        record = json.loads(archive.read(record_name))
        run = record["workflow_runs"][0]
        assert run["input"]["context"]["reference_urls"] == references
        assert run["meta"]["dynamic_script"]["params"]["references"] == references
        for reference in references:
            assert reference in run["meta"]["dynamic_script"]["script"]
            assert reference in record["messages"][-1]["content"]
        for message in record["messages"]:
            if message["content"].startswith("资料"):
                assert references[0] in message["content"]
                assert "<redacted-path>" in message["content"]
        for name in archive.namelist():
            content = archive.read(name).decode("utf-8")
            assert "private sample" not in content
            assert "private output" not in content
            assert "private-server" not in content
            assert "private-share" not in content
            assert "private-person" not in content
            assert str(tmp_path) not in content


def test_export_rejects_outside_root_and_unverifiable_run_identity(tmp_path: Path) -> None:
    """旧 WRROC 的安全相对产物兼容，逃逸路径和冒名关联不会进入清单。"""
    root = tmp_path / "records"
    root.mkdir()
    store = SessionStore(root / "sessions.db")
    sid = store.create_session("旧记录")
    safe = root / "data" / "legacy.csv"
    safe.parent.mkdir()
    safe.write_bytes(b"gene,padj\nG1,0.01\n")
    outside = tmp_path / "secret.csv"
    outside.write_text("private", encoding="utf-8")
    for directory, run_id in (("old-run", "old-run"), ("mismatch", "invented-run")):
        workflow = root / ".wrroc" / directory / "workflow.json"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "input": {"context": {"session_id": sid}},
                    "outputs": [
                        {"path": "data/legacy.csv"},
                        {"path": "../secret.csv"},
                        {"path": str(outside)},
                        {"path": str(root)},
                    ],
                }
            ),
            encoding="utf-8",
        )
    export_records("session", sid, out=root / "out.zip", hash_files=True, store=store, root=root)
    with zipfile.ZipFile(root / "out.zip") as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("artifacts.csv").decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["run_id"] == "old-run"
    assert rows[0]["path"] == str(Path("data") / "legacy.csv")
    assert rows[0]["sha256"] == "cf49c6000f39a465520ca1e071c7208cbae2bbaa6b4442e08ccb0d44bf77d940"


@pytest.mark.parametrize("scope", ["session", "project"])
def test_export_preserves_each_registered_run_when_outputs_share_a_file(
    tmp_path: Path, scope: str
) -> None:
    """跨 run 的共享路径保留两份已知关联，同 run 的目录/单文件重叠仅列一次。"""
    store = SessionStore(tmp_path / "sessions.db")
    project = store.create_project("共享旧产物")
    sid = store.create_session("同会话两次分析", project_id=project)
    directory = tmp_path / "data" / "projects" / project / "analysis" / "de"
    directory.mkdir(parents=True)
    shared = directory / "shared.csv"
    shared.write_bytes(b"gene,padj\nG1,0.01\n")
    recorder = WorkflowRecorder(tmp_path / ".wrroc", repo_root=tmp_path)
    identities = set()
    for _ in range(2):
        run_id = recorder.start_execution(
            {
                "type": "analysis",
                "analysis_type": "differential_expression",
                "original_input": "旧分析",
            },
            {},
            "旧分析",
            {"session_id": sid},
        )
        identities.add(run_id)
        recorder.record_output(run_id, name="csv", path=str(shared))
        recorder.record_output(run_id, name="directory", path=str(directory))
        recorder.save_run(run_id)
    out = tmp_path / "shared.zip"
    export_records(
        scope,
        sid if scope == "session" else project,
        out=out,
        hash_files=True,
        store=store,
        root=tmp_path,
    )
    with zipfile.ZipFile(out) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("artifacts.csv").decode("utf-8"))))
    assert len(rows) == 2
    assert {row["run_id"] for row in rows} == identities
    assert all(row["source"] == "wrroc_output" for row in rows)
    assert all(
        row["sha256"] == "cf49c6000f39a465520ca1e071c7208cbae2bbaa6b4442e08ccb0d44bf77d940"
        for row in rows
    )
