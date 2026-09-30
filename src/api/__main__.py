"""python -m src.api 的启动参数与浏览器工具：main() 由 T8 补齐。

端口 8600（避开开发期暂存的 Streamlit 8501）。前端构建产物存在时由
create_app 挂载在 / ，因此本入口在计划 2 之前同样可用（仅 API）。
"""
from __future__ import annotations

import logging
import threading
import time
import webbrowser

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8600


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    """延迟开浏览器（等服务起好）；任何失败都吞掉，不影响启动。"""
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - 无浏览器环境（CI/服务器）忽略
            logger.info("open browser skipped: %s", exc)

    threading.Thread(target=_open, name="open-browser", daemon=True).start()
