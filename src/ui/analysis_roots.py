"""分析产物根解析接缝：会话/项目归属由注入的 SessionStore 或其工厂决定。"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.ui.session_store import LEGACY_WORKSPACE_ID, SessionStore

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
        base = self._workspace_base(session)
        if session.get("project_id"):
            return (base / "projects" / session["project_id"] / "analysis").resolve()
        return (base / "analysis").resolve()

    def _workspace_base(self, session: Mapping[str, Any]) -> Path:
        """legacy 继续用 repo_root/data；新工作区用自己的文件根。"""
        workspace_id = session.get("workspace_id")
        if not workspace_id or workspace_id == LEGACY_WORKSPACE_ID:
            return (self._repo_root / "data").resolve()
        workspace = self._store().get_workspace(str(workspace_id))
        if workspace is None or workspace.get("status") != "active":
            raise ValueError("工作区不存在或已到期")
        root = workspace.get("root_path")
        if not root:
            return (self._repo_root / "data").resolve()
        return Path(root).resolve()

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
        data_root = (self._repo_root / "data").resolve()
        if not root.is_relative_to(data_root) or root.name != "sc_qc":
            raise ValueError("QC 目录非法，请重新 QC")
        relative = root.relative_to(data_root).parts
        # 允许：analysis/sc_qc、projects/<id>/analysis/sc_qc、
        # workspaces/<ws>/analysis/sc_qc、workspaces/<ws>/projects/<id>/analysis/sc_qc
        valid_shapes = (
            relative == ("analysis", "sc_qc")
            or (len(relative) == 4 and relative[0] == "projects")
            and relative[2:] == ("analysis", "sc_qc")
            or (len(relative) == 4 and relative[0] == "workspaces")
            and relative[2:] == ("analysis", "sc_qc")
            or (len(relative) == 6 and relative[0] == "workspaces" and relative[2] == "projects")
            and relative[4:] == ("analysis", "sc_qc")
        )
        if not valid_shapes:
            raise ValueError("QC 目录非法，请重新 QC")
        return root
