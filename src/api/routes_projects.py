"""项目 CRUD：项目是扁平命名容器，会话可属于项目或未归类。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.chat_service import session_lock
from src.ui.record_files import writable_dir
from src.ui.session_store import SessionStore
from src.ui.usage import project_usage

router = APIRouter(prefix="/api", tags=["projects"])


def _store() -> SessionStore:
    """每次新建短连接存储，与其它路由同策略。"""
    return SessionStore()


class ProjectCreateRequest(BaseModel):
    """POST /api/projects 请求体；label 缺省时取工作目录名。"""

    label: str | None = None
    root_path: str | None = None


class ProjectUpdateRequest(BaseModel):
    label: str | None = None
    root_path: str | None = None
    archived: bool | None = None


def _validated_root_path(value: str | None) -> str | None:
    """工作目录必须是绝对路径、已存在、是目录且可写；不自动创建或改动用户目录。"""
    if value is None or not value.strip():
        return None
    raw = Path(value.strip()).expanduser()
    if not raw.is_absolute():
        raise HTTPException(status_code=422, detail="工作目录必须是绝对路径")
    path = raw.resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail=f"工作目录不存在或不是目录：{path}")
    internal = [
        Path.cwd().resolve() / name for name in ("data", ".wrroc", "snapshots", ".cellspatio")
    ]
    if any(path == parent or path.is_relative_to(parent) for parent in internal):
        raise HTTPException(status_code=422, detail=f"工作目录不能位于应用数据目录内：{path}")
    if not writable_dir(path):
        raise HTTPException(status_code=422, detail=f"工作目录不可写：{path}")
    return str(path)


@router.get("/projects")
def list_projects(include_archived: bool = False) -> dict[str, Any]:
    """列出项目；默认隐藏已归档。"""
    return {"projects": _store().list_projects(include_archived=include_archived)}


@router.post("/projects")
def create_project(body: ProjectCreateRequest) -> dict[str, Any]:
    """新建项目；标签可省略，缺省取工作目录名。"""
    label = (body.label or "").strip()
    root_path = _validated_root_path(body.root_path)
    if not label:
        if root_path is None:
            raise HTTPException(status_code=422, detail="项目名不能为空")
        label = Path(root_path).name
        if not label:
            raise HTTPException(status_code=422, detail="无法从工作目录推导项目名，请填写项目名")
    store = _store()
    project_id = store.create_project(label, root_path=root_path)
    return {"project": store.get_project(project_id)}


@router.get("/projects/{project_id}/usage")
def get_project_usage(project_id: str) -> dict[str, Any]:
    """返回项目目录、会话产物与 kg_memory 的只读占用汇总。"""
    try:
        return project_usage(_store(), project_id, Path.cwd())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateRequest) -> dict[str, Any]:
    """更新项目；归档前先拿项目下所有会话锁，运行中拒绝。"""
    store = _store()
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if body.label is not None and not body.label.strip():
        raise HTTPException(status_code=422, detail="项目名不能为空")
    root_path = (
        _validated_root_path(body.root_path) if "root_path" in body.model_fields_set else None
    )
    locks = []
    if body.archived:
        sessions = [
            session
            for session in store.list_sessions(include_archived=False)
            if session["project_id"] == project_id
        ]
        for session in sessions:
            lock = session_lock(session["id"])
            if not lock.acquire(blocking=False):
                for held in locks:
                    held.release()
                raise HTTPException(status_code=409, detail="项目内有会话正在运行")
            locks.append(lock)
    try:
        store.update_project(
            project_id,
            label=body.label.strip() if body.label is not None else None,
            root_path=root_path,
            clear_root_path="root_path" in body.model_fields_set and root_path is None,
            archived=body.archived,
        )
    except ValueError as exc:
        status = 409 if "运行中" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    finally:
        for held in locks:
            held.release()
    return {"project": store.get_project(project_id)}
