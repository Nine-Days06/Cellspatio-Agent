"""SSE 事件契约：帧格式、tool label 补全、图表映射、消息线路编码。"""
from __future__ import annotations

import json


def test_sse_frame_exact_format_keeps_chinese_and_trailing_blank_line():
    from src.api.events import sse_frame

    frame = sse_frame({"type": "delta", "text": "你好"})

    assert frame == 'data: {"type": "delta", "text": "你好"}\n\n'
    assert frame.endswith("\n\n")
    assert json.loads(frame[len("data: "):-2]) == {"type": "delta", "text": "你好"}


def test_sse_frame_appends_label_from_tool_table():
    from src.api.events import sse_frame

    frame = sse_frame({"type": "tool_status", "name": "run_analysis", "phase": "start"})

    assert json.loads(frame[6:-2])["label"] == "运行分析"


def test_sse_frame_label_falls_back_to_tool_name_when_unmapped():
    from src.api.events import sse_frame

    frame = sse_frame({"type": "tool_status", "name": "mystery_tool", "phase": "end"})

    assert json.loads(frame[6:-2])["label"] == "mystery_tool"


def test_sse_frame_does_not_mutate_caller_event():
    from src.api.events import sse_frame

    event = {"type": "tool_status", "name": "query_memory", "phase": "start"}
    sse_frame(event)

    assert "label" not in event


def test_chart_events_maps_plotly_figure_to_plotly_json():
    import plotly.graph_objects as go

    from src.api.events import chart_events

    fig = go.Figure(go.Scatter(x=[1, 2], y=[3, 4]))

    events = chart_events({"charts": [fig]})

    assert len(events) == 1
    assert events[0]["type"] == "chart"
    assert "data" in events[0]["plotly_json"]


def test_chart_events_maps_image_chart_to_image_b64():
    from src.api.events import chart_events

    events = chart_events({"charts": [{"type": "image", "image_b64": "AAA"}]})

    assert events == [{"type": "chart", "image_b64": "AAA"}]


def test_chart_events_empty_for_missing_or_chartless_results():
    from src.api.events import chart_events

    assert chart_events(None) == []
    assert chart_events({}) == []
    assert chart_events({"charts": []}) == []
    assert chart_events({"charts": ["plain-dict"]}) == []


def test_wire_message_reencodes_plotly_figure_as_json_serialisable():
    import plotly.graph_objects as go

    from src.api.events import wire_message

    fig = go.Figure(go.Bar(x=["a"], y=[1]))
    wire = wire_message({"id": 3, "role": "assistant", "content": "ok",
                         "results": {"charts": [fig]}, "pending_script": None})

    assert wire["id"] == 3
    assert wire["pending_script"] is None
    assert wire["results"]["charts"][0]["type"] == "plotly"
    json.dumps(wire)  # 不得抛 TypeError


def test_wire_message_keeps_results_none():
    from src.api.events import wire_message

    assert wire_message({"id": 1, "content": "hi", "results": None})["results"] is None


def test_make_event_puts_type_first():
    from src.api.events import make_event

    assert make_event("done", message_id=7, message={"a": 1}) == {
        "type": "done", "message_id": 7, "message": {"a": 1},
    }