"""固定 R 阶段目录创建、日志与 metadata 收尾；不依赖 FastAPI 或 R 运行时。"""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.analysis.r_executor import RExecutorError

Fingerprints = Callable[[list[Path]], list[dict[str, Any]]]


def _text(value: str | bytes | None) -> str:
    """bytes 按 utf-8 宽松解码；None 视为空串。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def start_stage(directory: Path, *, create: bool = True) -> float:
    """创建唯一阶段目录并返回计时起点；create=False 时用于已建目录的阶段。"""
    if create:
        directory.mkdir(parents=True, exist_ok=False)
    return time.perf_counter()


def executor_streams(exc: BaseException) -> tuple[str, str]:
    """取 R 执行器保留的 stdout/stderr；超时优先用其 cause 的输出。"""
    if not isinstance(exc, RExecutorError):
        return "", ""
    stdout, stderr = exc.stdout, exc.stderr
    if isinstance(exc.__cause__, subprocess.TimeoutExpired):
        stdout, stderr = exc.__cause__.stdout, exc.__cause__.stderr
    return _text(stdout), _text(stderr)


def finish_stage(
    directory: Path,
    metadata: dict[str, Any],
    started: float,
    stdout: str | bytes,
    stderr: str | bytes,
    *,
    artifacts: bool,
    fingerprints: Fingerprints | None = None,
) -> None:
    """写 stdout/stderr、elapsed 和 metadata.json；可选登记产物指纹。"""
    if artifacts and fingerprints is None:
        raise ValueError("artifacts=True 需要调用方传入 fingerprints")
    for name, text in (("stdout.txt", stdout), ("stderr.txt", stderr)):
        (directory / name).write_text(_text(text), encoding="utf-8")
    metadata["elapsed_seconds"] = time.perf_counter() - started
    if artifacts and fingerprints is not None:
        records = fingerprints([path for path in directory.iterdir() if path.is_file()])
        metadata["artifacts"] = {Path(record["path"]).name: record for record in records}
    (directory / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
