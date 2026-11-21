"""分析产物根解析接缝：会话/项目归属由注入的 SessionStore 或其工厂决定。"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.ui.session_store import SessionStore

# 实例用于测试/控制层；工厂用于 app.state，保证会话查询跟随当前 store_factory。
StoreSource = SessionStore | Callable[[], SessionStore]


class AnalysisRoots:
    """持有 store 来源与仓库根，集中解析分析产物目录。"""

    def __init__(self, store: StoreSource, repo_root: str | Path):
        self._store_source = store
        self._repo_root = Path(repo_root).resolve()

    def _store(self) -> SessionStore:
        """实例直接复用；工厂每次调用重新取 store，由调用方决定生命周期。"""
        if isinstance(self._store_source, SessionStore):
            return self._store_source
        return self._store_source()

    def _session_root(self, session: Mapping[str, Any]) -> Path:
        """由已读取的会话行计算产物根，避免重复查询。"""
        base = self._repo_root / "data"
        if session.get("project_id"):
            return (base / "projects" / session["project_id"] / "analysis").resolve()
        return (base / "analysis").resolve()

    def for_session(self, session_id: str | None) -> Path:
        """新分析产物根：项目会话落 data/projects/<id>/analysis，其余沿用 data/analysis。"""
        if session_id:
            session = self._store().get_session(session_id)
            if session is None:
                raise ValueError("会话不存在，不能确定分析产物根")
            return self._session_root(session)
        return (self._repo_root / "data" / "analysis").resolve()

    def project_scoped(self, session_id: str | None) -> Path | None:
        """仅项目会话返回项目分析根；未归类或无会话返回 None，保留旧输出位置。"""
        if not session_id:
            return None
        session = self._store().get_session(session_id)
        if session is None:
            raise ValueError("会话不存在，不能确定分析产物根")
        if not session.get("project_id"):
            return None
        return self._session_root(session)

    def qc_root(self, run: Mapping[str, Any]) -> Path:
        """校验 run 目录在旧全局根或项目分析根内，返回 QC 根。"""
        root = Path(run["directory"]).resolve().parent
        legacy = (self._repo_root / "data" / "analysis" / "sc_qc").resolve()
        projects = (self._repo_root / "data" / "projects").resolve()
        project_root = (
            root.name == "sc_qc"
            and root.parent.name == "analysis"
            and root.parent.parent.parent == projects
        )
        if root != legacy and not project_root:
            raise ValueError("QC 目录非法，请重新 QC")
        return root
