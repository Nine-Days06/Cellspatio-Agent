"""UI 层只剩会话存储：Streamlit 前端已于计划 2 终态移除。

保留本包是因为 `session_store.py` 被 API 层复用（会话持久化，零改动约束）。
**不要**在本模块导入 streamlit 或任何前端框架——那会让 API 层凭空拉起 UI 依赖。
"""
from src.ui.session_store import SessionStore

__all__ = ["SessionStore"]
