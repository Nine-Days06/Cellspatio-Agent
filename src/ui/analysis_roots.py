"""分析产物根解析接缝：会话/项目归属由注入的 SessionStore 决定。"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.ui.session_store import SessionStore


class AnalysisRoots:
    """持有会话库与仓库根，集中解析分析产物目录。"""

    def __init__(self, store: SessionStore, repo_root: str | Path):
        self._store = store
        self._repo_root = Path(repo_root).resolve()

    def for_session(self, session_id: str | None) -> Path:
        """新分析产物根：项目会话落 data/projects/<id>/analysis，其余沿用 data/analysis。"""
        base = self._repo_root / "data"
        if session_id:
            session = self._store.get_session(session_id)
            if session is None:
                raise ValueError("会话不存在，不能确定分析产物根")
            if session.get("project_id"):
                return (base / "projects" / session["project_id"] / "analysis").resolve()
        return (base / "analysis").resolve()

    def project_scoped(self, session_id: str | None) -> Path | None:
        """仅项目会话返回项目分析根；未归类或无会话返回 None，保留旧输出位置。"""
        if not session_id:
            return None
        session = self._store.get_session(session_id)
        if session is None:
            raise ValueError("会话不存在，不能确定分析产物根")
        if not session.get("project_id"):
            return None
        return self.for_session(session_id)

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
