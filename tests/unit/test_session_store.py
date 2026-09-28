"""SessionStore CRUD 与结果序列化 roundtrip 测试。"""

import base64

from src.ui.session_store import SessionStore, _decode_results, _encode_results


def test_create_and_list_sessions():
    store = SessionStore()
    sid = store.create_session("第一个会话")
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert sessions[0]["title"] == "第一个会话"
    assert sessions[0]["message_count"] == 0


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
        sid, "assistant", "分析完成",
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
    store.update_session_meta(sid, summary="## 目标\nX", summary_upto=7,
                              downloaded_assets=[{"asset_id": "GSE1"}])
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
    store.append_message(sid, "assistant", "s1",
                         pending_script={"script": "a", "status": "pending"})
    store.append_message(sid, "assistant", "s2",
                         pending_script={"script": "b", "status": "pending"})
    store.set_script_status(sid, "expired")
    messages = store.get_messages(sid)
    assert messages[0]["pending_script"]["status"] == "pending"   # 旧的不动
    assert messages[1]["pending_script"]["status"] == "expired"   # 最新被改


def test_set_script_status_ignores_confirmed():
    store = SessionStore()
    sid = store.create_session()
    store.append_message(sid, "assistant", "s",
                         pending_script={"script": "a", "status": "confirmed"})
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
