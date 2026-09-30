"""SSE 事件契约：统一构造、帧序列化、图表与消息的线路编码。

事件类型共 7 类（与前端共享契约）：
- delta        {text}                          LLM 逐块输出
- tool_status  {name, phase, label}            tool-calling 循环过程
- confirm_card {kind, payload}                 needs_script / needs_data_confirmation
- chart        {plotly_json | image_b64}       分析产出图表
- compressed   {soft_warn}                     压缩软阈值提醒
- done         {message_id, message}           结果已持久化
- error        {message, retryable}            可恢复失败

前端契约注记：
① chart.plotly_json 是 JSON **字符串**，前端需 JSON.parse 后再喂 Plotly；
② done 载荷是规格 {message_id} 的超集，另含 message（完整线路消息，供对账）；
③ error.retryable 当前实现恒为 True（后端尚无不可恢复错误的细分）；
④ 打开会话必须带 ?restore=1，否则 pending_script 永不转 expired（见
   routes_sessions._restore_pending 的过期语义）。
"""
from __future__ import annotations

import json
from typing import Any

# 工具名 → 中文标签（UI 关注点，放后端统一给，前端不做映射表）
TOOL_LABELS: dict[str, str] = {
    "run_analysis": "运行分析",
    "search_datasets": "搜索数据集",
    "query_knowledge": "查询知识库",
    "query_memory": "查询记忆",
}


def make_event(event_type: str, **payload: Any) -> dict[str, Any]:
    """统一事件构造：首字段 type，其余为载荷。"""
    return {"type": event_type, **payload}


def sse_frame(event: dict[str, Any]) -> str:
    """事件 → SSE 帧（data: {json}\n\n）。tool_status 自动补 label。

    未知工具名回退为工具名本身；不修改调用方传入的 dict。
    """
    if event.get("type") == "tool_status":
        name = event.get("name") or ""
        event = {**event, "label": event.get("label") or TOOL_LABELS.get(name, name)}
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def chart_events(results: dict[str, Any] | None) -> list[dict[str, Any]]:
    """results → chart 事件列表。

    复用 session_store._encode_results（session_store 零改动）完成 Figure 编码，
    再把 {type: plotly, figure_json} / {type: image, image_b64} 映射为 chart 事件。
    """
    from src.ui.session_store import _encode_results

    if not results:
        return []
    payload = json.loads(_encode_results(results))
    events: list[dict[str, Any]] = []
    for chart in payload.get("charts") or []:
        if not isinstance(chart, dict):
            continue
        if chart.get("type") == "plotly":
            events.append(make_event("chart", plotly_json=chart["figure_json"]))
        elif chart.get("type") == "image":
            events.append(make_event("chart", image_b64=chart["image_b64"]))
    return events


def wire_message(msg: dict[str, Any]) -> dict[str, Any]:
    """SessionStore 读回的消息 → 可 JSON 序列化的线路格式。

    get_messages 已把 plotly dict 还原成 Figure，这里再编码回 JSON 文本，
    保证 done 事件与 REST 响应体都能直接 json.dumps。pending_script 原样透传。
    """
    from src.ui.session_store import _encode_results

    payload = dict(msg)
    results = payload.get("results")
    if results is not None:
        payload["results"] = json.loads(_encode_results(results))
    return payload
