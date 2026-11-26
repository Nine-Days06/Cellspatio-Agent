"""访客数据上传：文件只落当前工作区，并在进入 QC 前完成四层限额与压缩包校验。"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from src.api.auth import workspace_context
from src.api.chat_service import is_qc_stage, persist_outcome, register_qc_result
from src.api.deps import StoreDep
from src.api.events import wire_message
from src.api.session_locks import session_guard
from src.ui.workspace_cleanup import MAX_WORKSPACE_BYTES, tree_usage_bytes

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["input"])
logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_REQUEST_BYTES = 300 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 500 * 1024 * 1024
_CHUNK = 1024 * 1024
_MAX_ZIP_MEMBERS = 2000
_SESSION_ID = re.compile(r"[0-9a-f]{32}")


class UploadRejected(ValueError):
    """可预期拒绝；route 统一转 413/400 并清理半成品。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        """保存拒绝原因与对应的 HTTP 状态码。"""
        super().__init__(message)
        self.status_code = status_code


def _safe_name(filename: str | None) -> str:
    """只保留客户端文件名的 basename，拒绝空名、点目录与 NUL。"""
    name = Path(filename or "").name
    if not name or name in {".", ".."} or "\x00" in name:
        raise UploadRejected("文件名无效")
    return name


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """按 zip 外部属性判断成员是否为符号链接。"""
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def extract_zip(archive: Path, destination: Path) -> Path:
    """拒绝穿越、符号链接与超限解压；返回含 matrix.mtx 的 10x 目录。"""
    total = 0
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            if len(members) > _MAX_ZIP_MEMBERS:
                raise UploadRejected("压缩包文件数量过多")
            for info in members:
                raw = info.filename.replace("\\", "/")
                member = PurePosixPath(raw)
                if (
                    not raw
                    or member.is_absolute()
                    or ".." in member.parts
                    or "\x00" in raw
                    or info.flag_bits & 0x1
                    or _is_symlink(info)
                ):
                    raise UploadRejected("压缩包包含不安全路径或符号链接")
                if info.file_size > MAX_FILE_BYTES:
                    raise UploadRejected("压缩包内单个文件超过 100 MB", 413)
                total += info.file_size
                if total > MAX_REQUEST_BYTES:
                    raise UploadRejected("压缩包解压后超过 300 MB", 413)
                target = (destination / member).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise UploadRejected("压缩包路径越界")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=_CHUNK)
    except zipfile.BadZipFile as exc:
        raise UploadRejected("压缩包损坏或不是有效的 zip") from exc

    for directory in [destination, *(p for p in destination.rglob("*") if p.is_dir())]:
        names = {p.name for p in directory.iterdir() if p.is_file()}
        if (
            any(name in names for name in ("matrix.mtx", "matrix.mtx.gz"))
            and any(name in names for name in ("barcodes.tsv", "barcodes.tsv.gz"))
            and any(
                name in names
                for name in ("features.tsv", "features.tsv.gz", "genes.tsv", "genes.tsv.gz")
            )
        ):
            return directory
    raise UploadRejected("压缩包中未找到 10x matrix.mtx/barcodes/features 三件套")


def _workspace_root(request: Request) -> Path:
    """返回当前工作区上传根；缺失或不位于 data/workspaces 下则拒绝。"""
    workspace = workspace_context(request)
    root = workspace.get("root_path")
    if not root:
        raise HTTPException(409, "工作区文件根未配置")
    path = Path(root).resolve()
    managed = (Path(request.app.state.repo_root) / "data" / "workspaces").resolve()
    if not path.is_relative_to(managed):
        raise HTTPException(409, "工作区文件根非法")
    return path


def _enforce_quota(base_uploads: int, target_dir: Path, root: Path, *, ephemeral: bool) -> None:
    """临时工作区检查累计 500 MB 与总占用 2 GB；管理账户不受此限。"""
    if not ephemeral:
        return
    if base_uploads + tree_usage_bytes(target_dir) > MAX_UPLOAD_TOTAL_BYTES:
        raise UploadRejected("临时工作区累计上传超过 500 MB", 413)
    if tree_usage_bytes(root) > MAX_WORKSPACE_BYTES:
        raise UploadRejected("临时工作区总占用超过 2 GB", 413)


@router.post("/upload")
async def upload_input(
    session_id: str,
    request: Request,
    store: StoreDep,
    file: Annotated[UploadFile, File(...)],
) -> dict[str, Any]:
    """保存上传并运行固定 QC；任何失败都会删除本次上传目录。"""
    if not _SESSION_ID.fullmatch(session_id) or store.get_session(session_id) is None:
        raise HTTPException(404, "会话不存在")
    workspace = workspace_context(request)
    is_ephemeral = workspace.get("kind") == "ephemeral"
    if not is_ephemeral:
        raise HTTPException(403, "管理账户请使用服务器本地路径入口")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_REQUEST_BYTES + 5 * 1024 * 1024:
        raise HTTPException(413, "单次上传超过 300 MB")

    name = _safe_name(file.filename)
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".zip"}:
        raise HTTPException(400, "只支持 counts CSV 或 10x 三件套 zip")

    root = _workspace_root(request)
    uploads = root / "uploads"
    base_uploads = tree_usage_bytes(uploads)
    target_dir = uploads / session_id / uuid.uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=False)
    archive = target_dir / f"source{suffix}"
    saved = 0
    try:
        remaining = MAX_REQUEST_BYTES
        with archive.open("wb") as dst:
            while chunk := await file.read(_CHUNK):
                saved += len(chunk)
                if saved > MAX_FILE_BYTES:
                    raise UploadRejected("单个文件超过 100 MB", 413)
                if saved > remaining:
                    raise UploadRejected("单次上传超过 300 MB", 413)
                dst.write(chunk)
        _enforce_quota(base_uploads, target_dir, root, ephemeral=is_ephemeral)

        if suffix == ".zip":
            extracted = target_dir / "extracted"
            input_path = extract_zip(archive, extracted)
            archive.unlink(missing_ok=True)
            if tree_usage_bytes(extracted) > MAX_REQUEST_BYTES:
                raise UploadRejected("压缩包解压后超过 300 MB", 413)
            _enforce_quota(base_uploads, target_dir, root, ephemeral=is_ephemeral)
        else:
            input_path = archive
            try:
                input_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise UploadRejected("CSV 必须是 UTF-8 文本") from exc

        with session_guard(session_id):
            if store.get_session(session_id) is None:
                raise HTTPException(404, "会话不存在")
            agent = request.app.state.agent
            result = agent.workflow_manager.run_analysis_for_agent(
                "single_cell",
                {"input_files": [str(input_path)]},
                {"last_user_input": f"上传数据：{name}", "session_id": session_id},
            )
            if not is_qc_stage(result):
                raise UploadRejected(result.get("message") or "输入不是有效的单细胞数据")
            result = register_qc_result(agent, store, session_id, result)
            if result.get("status") != "error":
                store.replace_local_asset(
                    session_id,
                    {
                        "source": "upload",
                        "asset_id": name,
                        "access_path": str(input_path),
                    },
                )
            result, last = persist_outcome(store, session_id, result)
            return {
                "message": wire_message(last),
                "session": store.get_session(session_id),
                "qc_run_id": (result.get("results") or {}).get("qc_run_id"),
                "status": result.get("status"),
            }
    except UploadRejected as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(exc.status_code, str(exc)) from exc
    except HTTPException:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    except (OSError, ValueError, sqlite3.Error) as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        logger.warning("upload input failed for %s: %s", session_id, exc)
        raise HTTPException(409, str(exc)) from exc
