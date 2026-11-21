"""受控分析阶段共用的“授权一次运行”编排。"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from fastapi import HTTPException

from src.api.session_locks import lock_for as session_lock

RunT = TypeVar("RunT", bound=Mapping[str, Any])
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageClaim:
    """通过证据校验后的一次领取计划。

    阶段提供 attempt 目录及其父目录，seam 只负责校验目录未被占用且直接位于父目录下。
    """

    directory_parent: Path
    directory: Path
    start: Callable[[], dict[str, Any]]
    execute: Callable[[dict[str, Any]], dict[str, Any]]
    finish: Callable[[dict[str, Any]], dict[str, Any] | None]
    on_error: Callable[[Exception], dict[str, Any]]


def run_authorized(
    *,
    session_id: str,
    load: Callable[[], RunT],
    previous: Callable[[RunT], dict[str, Any] | None],
    prepare: Callable[[RunT, str], StageClaim],
    refresh: Callable[[], dict[str, Any]],
    unsafe_directory: str,
) -> dict[str, Any]:
    """执行一次已授权分析；历史、锁、目录、领取和失败收口只在这里实现。

    ``prepare`` 必须在锁内、重读状态后完成证据校验，并返回领取计划。
    抢锁前后都重查历史：重复请求已落库则返回历史，抢锁失败且无历史才是忙。
    """
    run = load()
    if previous(run) is not None:
        return refresh()

    lock = session_lock(session_id)
    if not lock.acquire(blocking=False):
        if previous(load()) is not None:
            return refresh()
        raise HTTPException(409, "该会话已有任务在进行")
    try:
        run = load()
        if previous(run) is not None:
            return refresh()
        attempt_id = uuid.uuid4().hex
        claim = prepare(run, attempt_id)
        base = claim.directory_parent.resolve()
        directory = claim.directory
        if directory.parent.resolve() != base or directory.name != attempt_id or directory.exists():
            raise HTTPException(409, unsafe_directory)
        attempt = claim.start()
        if attempt["claimed"]:
            try:
                result = claim.execute(attempt)
            except OSError as exc:
                result = claim.on_error(exc)
            try:
                saved = claim.finish(result)
            except Exception as exc:
                # 收尾失败若直接抛出，attempt 会永久停在 running 并锁死后续阶段。
                logger.exception("阶段收尾失败，改落失败终态")
                result = claim.on_error(exc)
                saved = claim.finish(result)
            if saved is not None and saved != result:
                (directory / "metadata.json").write_text(
                    json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return refresh()
    finally:
        lock.release()
