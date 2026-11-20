"""共享授权执行编排的历史、锁、领取与失败收口。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from src.api.authorized_run import StageClaim, run_authorized
from src.api.chat_service import session_lock


def _run(tmp_path: Path) -> dict[str, str]:
    """假运行记录；目录只需满足 seam 的父目录校验。"""
    return {"directory": str(tmp_path / "qc_root" / "run-1")}


def _claim(
    tmp_path: Path,
    attempt_id: str,
    *,
    start: Any = None,
    execute: Any = None,
    finish: Any = None,
    on_error: Any = None,
    directory: Path | None = None,
) -> StageClaim:
    """构造默认领取成功、执行完成的阶段计划。"""
    directory_parent = tmp_path / "sc_filter"
    directory = directory or directory_parent / attempt_id
    return StageClaim(
        directory_parent=directory_parent,
        directory=directory,
        start=start or (lambda: {"attempt_id": attempt_id, "claimed": True}),
        execute=execute or (lambda attempt: {"status": "completed"}),
        finish=finish or (lambda result: None),
        on_error=on_error or (lambda exc: {"status": "failed"}),
    )


def _call(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    """用可控回调调用共享执行 seam。"""
    params: dict[str, Any] = {
        "session_id": "s1",
        "load": lambda: _run(tmp_path),
        "previous": lambda run: None,
        "prepare": lambda run, attempt_id: _claim(tmp_path, attempt_id),
        "refresh": lambda: {"attempts": []},
        "unsafe_directory": "过滤输出目录非法",
    }
    params.update(overrides)
    return run_authorized(**params)


def test_existing_attempt_returns_history_without_claiming(tmp_path: Path) -> None:
    prepared: list[str] = []

    def prepare(run: dict[str, Any], attempt_id: str) -> StageClaim:
        prepared.append(attempt_id)
        raise AssertionError("must not prepare a duplicate")

    result = _call(
        tmp_path,
        previous=lambda run: {"attempt_id": "old"},
        prepare=prepare,
        refresh=lambda: {"attempts": ["old"]},
    )

    assert result == {"attempts": ["old"]}
    assert prepared == []


def test_unavailable_lock_returns_history_when_attempt_landed(tmp_path: Path) -> None:
    attempts = iter([None, {"attempt_id": "old"}])
    prepared: list[str] = []
    lock = session_lock("s1")
    assert lock.acquire(blocking=False)
    try:
        result = _call(
            tmp_path,
            previous=lambda run: next(attempts),
            prepare=lambda run, attempt_id: prepared.append(attempt_id),
            refresh=lambda: {"attempts": ["old"]},
        )
    finally:
        lock.release()

    assert result == {"attempts": ["old"]}
    assert prepared == []


def test_unavailable_lock_without_history_is_busy(tmp_path: Path) -> None:
    lock = session_lock("s1")
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as excinfo:
            _call(tmp_path)
    finally:
        lock.release()

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "该会话已有任务在进行"


def test_claimed_oserror_finishes_failed_and_releases_lock(tmp_path: Path) -> None:
    finished: list[dict[str, Any]] = []
    seen: dict[str, Path] = {}

    def prepare(run: dict[str, Any], attempt_id: str) -> StageClaim:
        def execute(attempt: dict[str, Any]) -> dict[str, Any]:
            raise OSError("disk full")

        claim = _claim(
            tmp_path,
            attempt_id,
            execute=execute,
            finish=finished.append,
            on_error=lambda exc: {
                "status": "failed",
                "stage": "filter_review",
                "scientific_status": "unreviewed",
                "error": str(exc),
            },
        )
        seen["directory"] = claim.directory
        return claim

    result = _call(tmp_path, prepare=prepare, refresh=lambda: {"attempts": ["failed"]})

    assert result == {"attempts": ["failed"]}
    assert finished == [
        {
            "status": "failed",
            "stage": "filter_review",
            "scientific_status": "unreviewed",
            "error": "disk full",
        }
    ]
    assert seen["directory"].parent == tmp_path / "sc_filter"
    assert session_lock("s1").acquire(blocking=False)
    session_lock("s1").release()


def test_unclaimed_attempt_skips_execution(tmp_path: Path) -> None:
    executed: list[str] = []

    def prepare(run: dict[str, Any], attempt_id: str) -> StageClaim:
        return _claim(
            tmp_path,
            attempt_id,
            start=lambda: {"attempt_id": attempt_id, "claimed": False},
            execute=lambda attempt: executed.append(attempt["attempt_id"]) or {},
        )

    _call(tmp_path, prepare=prepare)

    assert executed == []
    assert session_lock("s1").acquire(blocking=False)
    session_lock("s1").release()


def test_existing_directory_is_rejected_before_claiming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = "fixed"
    (tmp_path / "sc_filter" / attempt_id).mkdir(parents=True)
    monkeypatch.setattr(
        "src.api.authorized_run.uuid.uuid4",
        lambda: SimpleNamespace(hex=attempt_id),
    )
    started: list[str] = []

    def prepare(run: dict[str, Any], attempt_id: str) -> StageClaim:
        return _claim(tmp_path, attempt_id, start=lambda: started.append(attempt_id))

    with pytest.raises(ValueError, match="过滤输出目录非法"):
        _call(tmp_path, prepare=prepare)

    assert started == []
    assert session_lock("s1").acquire(blocking=False)
    session_lock("s1").release()
    assert session_lock("s1").acquire(blocking=False)
    session_lock("s1").release()


def test_directory_outside_parent_is_rejected(tmp_path: Path) -> None:
    started: list[str] = []

    def prepare(run: dict[str, Any], attempt_id: str) -> StageClaim:
        return _claim(
            tmp_path,
            attempt_id,
            directory=tmp_path / "elsewhere" / attempt_id,
            start=lambda: started.append(attempt_id),
        )

    with pytest.raises(ValueError, match="过滤输出目录非法"):
        _call(tmp_path, prepare=prepare)

    assert started == []
