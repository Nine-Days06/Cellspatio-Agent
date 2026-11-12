"""项目 CRUD：项目是扁平命名容器，会话可属于项目或未归类。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.ui.session_store import SessionStore

router = APIRouter(prefix="/api", tags=["projects"])


def _store() -> SessionStore:
    return SessionStore()


class ProjectCreateRequest(BaseModel):
    label: str
    root_path: str | None = None


class ProjectUpdateRequest(BaseModel):
    label: str | None = None
    root_path: str | None = None
    archived: bool | None = None


@router.get("/projects")
def list_projects(include_archived: bool = False) -> dict[str, Any]:
    return {"projects": _store().list_projects(include_archived=include_archived)}


@router.post("/projects")
def create_project(body: ProjectCreateRequest) -> dict[str, Any]:
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="项目名不能为空")
    store = _store()
    project_id = store.create_project(label, root_path=(body.root_path or None))
    return {"project": store.get_project(project_id)}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateRequest) -> dict[str, Any]:
    store = _store()
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if body.label is not None and not body.label.strip():
        raise HTTPException(status_code=422, detail="项目名不能为空")
    try:
        store.update_project(
            project_id,
            label=body.label.strip() if body.label is not None else None,
            root_path=body.root_path,
            clear_root_path="root_path" in body.model_fields_set and body.root_path is None,
            archived=body.archived,
        )
    except ValueError as exc:
        status = 409 if "运行中" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {"project": store.get_project(project_id)}
