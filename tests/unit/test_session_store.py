"""SessionStore CRUD 与结果序列化 roundtrip 测试。"""

import base64
import sqlite3

import pytest

from src.ui.session_store import (
    SessionStore,
    _decode_results,
    _encode_results,
    project_analysis_root,
)


def test_create_and_list_sessions():
    store = SessionStore()
    sid = store.create_session("第一个会话")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert sessions[0]["title"] == "第一个会话"
    assert sessions[0]["message_count"] == 0


def test_migrates_legacy_sessions_table(tmp_path):
    """旧库只有原始 sessions 列时，打开后补 project_id/pinned/archived_at 且不丢数据。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '新会话',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            summary TEXT,
            summary_upto INTEGER NOT NULL DEFAULT 0,
            downloaded_assets TEXT NOT NULL DEFAULT '[]'
        );
        INSERT INTO sessions (id, title, created_at, updated_at)
        VALUES ('old', '旧会话', '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    store = SessionStore(db)
    session = store.get_session("old")
    assert session is not None
    assert session["title"] == "旧会话"
    assert session["project_id"] is None
    assert session["pinned"] is False
    assert session["archived_at"] is None


def test_project_crud_and_session_grouping():
    store = SessionStore()
    pid = store.create_project("肝癌项目", "D:/data/liver")
    sid = store.create_session("分析", project_id=pid)

    project = store.list_projects()[0]
    assert project["label"] == "肝癌项目"
    assert project["root_path"] == "D:/data/liver"
    assert project["session_count"] == 1
    assert store.list_sessions()[0]["project_id"] == pid

    store.set_session_pinned(sid, True)
    assert store.list_sessions()[0]["pinned"] is True

    store.archive_session(sid)
    assert store.list_sessions() == []
    archived = store.list_sessions(include_archived=True)[0]
    assert archived["archived_at"] is not None
    assert store.list_projects()[0]["session_count"] == 0

    store.move_session(sid, None)
    store.update_project(pid, archived=True)
    assert store.list_projects() == []
    assert store.list_projects(include_archived=True)[0]["archived_at"] is not None

    store.update_project(pid, archived=False)
    store.delete_project(pid)
    assert store.get_project(pid) is None


def test_delete_non_empty_project_is_blocked():
    store = SessionStore()
    pid = store.create_project("非空")
    store.create_session("s", project_id=pid)
    with pytest.raises(ValueError, match="项目仍有会话"):
        store.delete_project(pid)


def test_move_session_to_unknown_or_archived_project_rejected():
    store = SessionStore()
    sid = store.create_session("s")
    with pytest.raises(ValueError, match="项目不存在"):
        store.move_session(sid, "nope")

    pid = store.create_project("已归档")
    store.update_project(pid, archived=True)
    with pytest.raises(ValueError, match="项目已归档"):
        store.move_session(sid, pid)


def test_archived_project_hides_its_sessions_and_rejects_new_ones():
    store = SessionStore()
    pid = store.create_project("归档项目")
    sid = store.create_session("会话", project_id=pid)
    store.update_project(pid, archived=True)

    assert store.list_sessions() == []
    assert store.list_sessions(include_archived=True)[0]["id"] == sid
    with pytest.raises(ValueError, match="项目已归档"):
        store.create_session("新会话", project_id=pid)


def test_archive_project_rejects_running_session(monkeypatch):
    store = SessionStore()
    pid = store.create_project("运行中项目")
    store.create_session("会话", project_id=pid)
    monkeypatch.setattr(store, "running_stage", lambda _sid: "PCA")

    with pytest.raises(ValueError, match="运行中"):
        store.update_project(pid, archived=True)


def test_project_analysis_root_rejects_unknown_session(tmp_path):
    with pytest.raises(ValueError, match="会话不存在"):
        project_analysis_root("nope", tmp_path)


def test_append_message_sets_title_from_first_user_message():
    store = SessionStore()
    sid = store.create_session("新会话")
    store.append_message(sid, "user", "请帮我做差异表达分析，谢谢")
    session = store.get_session(sid)
    assert session["title"] == "请帮我做差异表达分析，谢谢"[:20]
    messages = store.get_messages(sid)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["seq"] == 1


def test_append_and_get_messages_roundtrip_with_results():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(
        sid,
        "assistant",
        "分析完成",
        results={"statistics": {"n": 3}, "data": [{"gene": "TP53"}]},
        pending_script={"script": "print(1)", "status": "pending"},
    )
    msg = store.get_messages(sid)[0]
    assert msg["content"] == "分析完成"
    assert msg["results"]["statistics"] == {"n": 3}
    assert msg["pending_script"]["status"] == "pending"


def test_delete_session_cascades_messages():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "user", "hi")
    store.delete_session(sid)
    assert store.get_session(sid) is None
    assert store.get_messages(sid) == []
    assert store.list_sessions() == []


def test_update_session_meta_roundtrip():
    store = SessionStore()
    sid = store.create_session()
    store.update_session_meta(
        sid, summary="## 目标\nX", summary_upto=7, downloaded_assets=[{"asset_id": "GSE1"}]
    )
    session = store.get_session(sid)
    assert session["summary"] == "## 目标\nX"
    assert session["summary_upto"] == 7
    assert session["downloaded_assets"] == [{"asset_id": "GSE1"}]


def test_update_session_meta_none_means_untouched():
    store = SessionStore()
    sid = store.create_session()
    store.update_session_meta(sid, summary="已写入", summary_upto=3)
    store.update_session_meta(sid)  # 全 None：不改 summary
    session = store.get_session(sid)
    assert session["summary"] == "已写入"
    assert session["summary_upto"] == 3


def test_set_script_status_updates_latest_pending():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(
        sid, "assistant", "s1", pending_script={"script": "a", "status": "pending"}
    )
    store.append_message(
        sid, "assistant", "s2", pending_script={"script": "b", "status": "pending"}
    )
    store.set_script_status(sid, "expired")
    messages = store.get_messages(sid)
    assert messages[0]["pending_script"]["status"] == "pending"  # 旧的不动
    assert messages[1]["pending_script"]["status"] == "expired"  # 最新被改


def test_set_script_status_ignores_confirmed():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(
        sid, "assistant", "s", pending_script={"script": "a", "status": "confirmed"}
    )
    store.set_script_status(sid, "expired")
    assert store.get_messages(sid)[0]["pending_script"]["status"] == "confirmed"


def test_encode_decode_plotly_figure_roundtrip():
    import plotly.express as px

    fig = px.bar(x=["a", "b"], y=[1, 2])
    text = _encode_results({"charts": [fig], "statistics": {"n": 2}})
    restored = _decode_results(text)
    assert restored["statistics"] == {"n": 2}
    chart = restored["charts"][0]
    assert hasattr(chart, "to_dict")
    assert list(chart.data[0].x) == ["a", "b"]


def test_encode_matplotlib_figure_to_image_b64():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4])
    text = _encode_results({"charts": [fig]})
    restored = _decode_results(text)
    chart = restored["charts"][0]
    assert chart["type"] == "image"
    raw = base64.b64decode(chart["image_b64"])
    assert raw[:4] == b"\x89PNG"
    plt.close(fig)
