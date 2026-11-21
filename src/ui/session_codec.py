"""会话消息的时间戳与结果编码；阶段操作与 SessionStore 共用。"""

from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    """UTC ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def _encode_results(results: dict[str, Any] | None) -> str | None:
    """results → JSON；plotly Figure 内嵌 json，matplotlib Figure 转 PNG base64。"""
    if results is None:
        return None
    payload: dict[str, Any] = dict(results)
    charts = payload.get("charts")
    if isinstance(charts, list):
        encoded = []
        for chart in charts:
            if hasattr(chart, "savefig"):  # matplotlib Figure
                buf = io.BytesIO()
                chart.savefig(buf, format="png", dpi=120, bbox_inches="tight")
                encoded.append(
                    {
                        "type": "image",
                        "image_b64": base64.b64encode(buf.getvalue()).decode(),
                    }
                )
            elif hasattr(chart, "to_json"):  # plotly Figure
                encoded.append({"type": "plotly", "figure_json": chart.to_json()})
            else:
                encoded.append(chart)  # 普通 dict 原样
        payload["charts"] = encoded
    return json.dumps(payload, ensure_ascii=False, default=str)
