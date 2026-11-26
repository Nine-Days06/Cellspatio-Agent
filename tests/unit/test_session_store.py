"""SessionStore CRUD 与结果序列化 roundtrip 测试。"""

import base64
import sqlite3

import pytest

from src.ui.session_store import (
    SCHEMA_VERSION,
    SessionStore,
    _decode_results,
    _encode_results,
)
from src.ui.store_errors import StoreError


def test_create_and_list_sessions():
    store = SessionStore()
    sid = store.create_session("第一个会话")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert sessions[0]["title"] == "第一个会话"
    assert sessions[0]["message_count"] == 0
    assert store.get_session(sid)["title_source"] == "explicit"


def test_create_session_without_title_is_default_source():
    """未给标题 = default，才能被首条消息覆盖。"""
    store = SessionStore()
    sid = store.create_session()
    session = store.get_session(sid)
    assert session["title"] == "新会话"
    assert session["title_source"] == "default"


def test_append_message_returns_inserted_row() -> None:
    """追加即返回落库身份，调用方不必再读整段历史。"""
    store = SessionStore()
    sid = store.create_session()

    msg = store.append_message(sid, "user", "分析这份数据", results={"steps": [1]})

    assert (msg["session_id"], msg["seq"], msg["role"]) == (sid, 1, "user")
    assert msg["results"] == {"steps": [1]}
    assert msg["id"] == store.get_messages(sid)[-1]["id"]


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
    assert session["title_source"] == "derived"
    assert session["project_id"] is None
    assert session["pinned"] is False
    assert session["archived_at"] is None


def test_schema_version_is_stamped(tmp_path) -> None:
    """首次打开写版本号，后续连接可跳过建表与迁移。"""
    store = SessionStore(tmp_path / "v.db")
    store.create_session()

    conn = sqlite3.connect(tmp_path / "v.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


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


def test_project_archive_check_reads_inside_write_transaction(monkeypatch):
    """归档校验必须在写事务内读库，不能信任事务外的 get_project 快照。"""
    store = SessionStore()
    pid = store.create_project("归档项目")
    sid = store.create_session("会话")
    store.update_project(pid, archived=True)
    # 事务外快照若被当作权威，会放行归档后的写入。
    monkeypatch.setattr(
        SessionStore,
        "get_project",
        lambda self, project_id: {"id": project_id, "archived_at": None},
    )

    with pytest.raises(ValueError, match="项目已归档"):
        store.create_session("新会话", project_id=pid)
    with pytest.raises(ValueError, match="项目已归档"):
        store.move_session(sid, pid)


def test_archive_project_checks_running_inside_write_transaction():
    """归档项目不再走 running_stage 预检，而是在写事务内用同一连接查询。"""
    store = SessionStore()
    pid = store.create_project("运行中项目")
    sid = store.create_session("会话", project_id=pid)
    _seed_running_filter(store, sid)

    with pytest.raises(ValueError, match="过滤运行中，不能归档项目"):
        store.update_project(pid, archived=True)
    assert store.get_project(pid)["archived_at"] is None


def _seed_running_filter(store: SessionStore, session_id: str) -> None:
    """在写事务外插入一个 running 过滤 attempt，模拟并发领取。"""
    now = "2026-11-12T00:00:00+00:00"
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO qc_runs (run_id, session_id, directory, evidence) VALUES (?, ?, ?, '{}')",
            ("run-x", session_id, "sc_qc"),
        )
        conn.execute(
            "INSERT INTO qc_previews (preview_id, run_id, payload) VALUES ('p1', 'run-x', '{}')"
        )
        conn.execute(
            "INSERT INTO sc_filter_attempts "
            "(attempt_id, run_id, preview_id, binding_sha256, tool_version, directory, status, "
            "created_at) VALUES ('f1', 'run-x', 'p1', 'a', '1', 'sc_filter/f1', 'running', ?)",
            (now,),
        )


def test_delete_session_rejects_running_stage():
    """预检与删除在同一写事务内：运行中的阶段必须拦住删除。"""
    store = SessionStore()
    sid = store.create_session()
    _seed_running_filter(store, sid)

    with pytest.raises(ValueError, match="过滤运行中，不能删除会话"):
        store.delete_session(sid)
    assert store.get_session(sid) is not None


def test_archive_session_rejects_running_stage():
    """归档同样在写事务内复检，避免检查后被并发领取。"""
    store = SessionStore()
    sid = store.create_session()
    _seed_running_filter(store, sid)

    with pytest.raises(ValueError, match="过滤运行中，不能归档会话"):
        store.archive_session(sid)
    assert store.get_session(sid)["archived_at"] is None


def test_batch_delete_rolls_back_when_a_later_session_is_running():
    """批量删除必须整体失败：先删的会话不能在回滚后消失。"""
    store = SessionStore()
    first = store.create_session("一")
    second = store.create_session("二")
    _seed_running_filter(store, second)

    with pytest.raises(ValueError, match="过滤运行中，不能删除会话"):
        store.delete_sessions_and_project([first, second])
    assert store.get_session(first) is not None
    assert store.get_session(second) is not None


def test_append_message_sets_title_from_first_user_message():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "user", "请帮我做差异表达分析，谢谢")
    session = store.get_session(sid)
    assert session["title"] == "请帮我做差异表达分析，谢谢"[:20]
    assert session["title_source"] == "derived"
    messages = store.get_messages(sid)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["seq"] == 1


def test_explicit_title_survives_first_user_message():
    """显式标题（上传/本地路径入口）不被首条消息覆盖。"""
    store = SessionStore()
    sid = store.create_session("上传 10x 数据")
    store.append_message(sid, "user", "完全不同的提问内容，不应该变成标题")
    session = store.get_session(sid)
    assert session["title"] == "上传 10x 数据"
    assert session["title_source"] == "explicit"


def test_model_title_skips_explicit_and_model_sources():
    """自动命名只覆盖 default/derived，不覆盖 explicit/model。"""
    store = SessionStore()
    explicit = store.create_session("用户命名")
    assert store.set_session_title(explicit, "模型标题", source="model") is False
    assert store.get_session(explicit)["title"] == "用户命名"

    derived = store.create_session()
    store.append_message(derived, "user", "首条提问内容")
    assert store.set_session_title(derived, "模型标题", source="model") is True
    session = store.get_session(derived)
    assert (session["title"], session["title_source"]) == ("模型标题", "model")

    # 已由模型命名后不再被再次覆盖
    assert store.set_session_title(derived, "第二个标题", source="model") is False
    assert store.get_session(derived)["title"] == "模型标题"


def test_explicit_title_overwrites_any_source():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "user", "首条提问内容")
    assert store.set_session_title(sid, "用户改名", source="explicit") is True
    session = store.get_session(sid)
    assert (session["title"], session["title_source"]) == ("用户改名", "explicit")
    assert store.set_session_title(sid, "模型标题", source="model") is False


def test_set_session_title_rejects_blank():
    store = SessionStore()
    sid = store.create_session()
    with pytest.raises(StoreError) as exc:
        store.set_session_title(sid, "   ", source="explicit")
    assert exc.value.kind == "invalid"
    assert store.get_session(sid)["title"] == "新会话"


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


def test_release_script_only_reverts_own_claim():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "assistant", "s", pending_script={"script": "a", "status": "pending"})
    claimed_id, pending = store.claim_script(sid, "confirmed")
    token = pending["claim_token"]

    store.release_script(sid, claimed_id, "not-my-token")
    assert store.get_messages(sid)[0]["pending_script"]["status"] == "confirmed"

    store.release_script(sid, claimed_id, token)
    restored = store.get_messages(sid)[0]["pending_script"]
    assert restored["status"] == "pending"
    assert "claim_token" not in restored


def test_release_script_does_not_clobber_later_claim():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "assistant", "s", pending_script={"script": "a", "status": "pending"})
    claimed_id, first = store.claim_script(sid, "confirmed")
    store.release_script(sid, claimed_id, first["claim_token"])

    # 另一个 worker 已重新领取并开始执行；旧令牌的回滚不得把它打回 pending。
    store.claim_script(sid, "confirmed")
    store.release_script(sid, claimed_id, first["claim_token"])
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


def test_new_store_does_not_create_knowledge_qa_reviews_table(tmp_path):
    """新库不再创建知识问答复核表，也没有对应读写入口。"""
    db_path = tmp_path / "sessions.db"
    store = SessionStore(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert "knowledge_qa_reviews" not in tables
    assert not hasattr(store, "record_knowledge_qa_review")
    assert not hasattr(store, "get_knowledge_qa_reviews")


def test_existing_store_keeps_knowledge_qa_reviews_table(tmp_path):
    """旧库中已有的知识问答复核表保留原数据，不迁移也不删除。"""
    db_path = tmp_path / "sessions.db"
    with sqlite3.connect(db_path) as conn:
        # 复刻旧版建表语句的全部约束，验证打开旧库不会被结构差异卡住。
        conn.execute(
            "CREATE TABLE knowledge_qa_reviews ("
            "review_id TEXT PRIMARY KEY, answer_id TEXT NOT NULL, binding_sha256 TEXT NOT NULL, "
            "record_version TEXT NOT NULL, source_revision INTEGER NOT NULL, "
            "decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')), "
            "veto INTEGER NOT NULL DEFAULT 0 CHECK(veto IN (0, 1)), reason TEXT NOT NULL, "
            "reviewer TEXT NOT NULL, created_at TEXT NOT NULL, "
            "UNIQUE(answer_id, binding_sha256, record_version, source_revision))"
        )
        conn.execute(
            "CREATE INDEX idx_knowledge_qa_reviews_answer "
            "ON knowledge_qa_reviews(answer_id, created_at)"
        )
        conn.execute(
            "INSERT INTO knowledge_qa_reviews VALUES "
            "('r1', 'a1', 'b1', 'v1', 1, 'accepted', 0, '理由', 'user', '2026-01-01T00:00:00')"
        )

    SessionStore(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT review_id FROM knowledge_qa_reviews").fetchall()
    assert rows == [("r1",)]


def test_move_session_rejects_running_stage():
    """运行中换项目会让后续产物落到新根，必须在写事务内拦住。"""
    store = SessionStore()
    source = store.create_project("源项目", "D:/data/a")
    target = store.create_project("目标项目", "D:/data/b")
    sid = store.create_session(project_id=source)
    _seed_running_filter(store, sid)

    with pytest.raises(StoreError, match="过滤运行中，不能移动会话"):
        store.move_session(sid, target)
    assert store.get_session(sid)["project_id"] == source

    store.move_session(sid, source)
    assert store.get_session(sid)["project_id"] == source
