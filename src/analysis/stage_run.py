"""固定 R 阶段目录创建、日志与 metadata 收尾；不依赖 FastAPI 或 R 运行时。"""

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.analysis.evidence import fingerprints as default_fingerprints
from src.analysis.r_executor import RExecutorError

Fingerprints = Callable[[list[Path]], list[dict[str, Any]]]


@dataclass(frozen=True)
class PreparedStage:
    """阶段准备结果：source 用于前后比对，data 供脚本/校验使用。"""

    source: Any = None
    data: Any = None


@dataclass(frozen=True)
class FixedStageRun:
    """四个固定 R 阶段共享的执行计划。"""

    directory: Path
    metadata: dict[str, Any]
    prepare: Callable[[], PreparedStage]
    script: Callable[[PreparedStage], str]
    check: Callable[[PreparedStage], dict[str, Any]]
    after: Callable[[PreparedStage], Any]
    returncode_error: str
    script_error: str
    errors: tuple[type[BaseException], ...]
    source_keys: tuple[str, str] | None = None
    # PCA 的 metadata 只存 source，前后比对仍看完整 checked_parent 结果。
    source_projection: Callable[[Any], Any] | None = None
    source_error: str | None = None
    failure_error: str | None = None
    fingerprint: Fingerprints = default_fingerprints


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


def run_fixed_stage(plan: FixedStageRun, executor: Any) -> dict[str, Any]:
    """执行一次固定 R 阶段；控制流、失败收口与产物登记只在这里实现。"""
    started = start_stage(plan.directory)
    stdout = stderr = ""
    try:
        prepared = plan.prepare()
        if plan.source_keys is not None:
            plan.metadata[plan.source_keys[0]] = (
                plan.source_projection(prepared.source)
                if plan.source_projection is not None
                else prepared.source
            )
        script = plan.directory / "script.R"
        code = plan.script(prepared)
        script.write_text(code, encoding="utf-8")
        completed = executor.execute_script(str(script))
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        plan.metadata["returncode"] = completed.returncode
        if completed.returncode != 0:
            raise ValueError(plan.returncode_error)
        summary = plan.check(prepared)
        if script.read_text(encoding="utf-8") != code:
            raise ValueError(plan.script_error)
        after = plan.after(prepared)
        if plan.source_keys is not None:
            plan.metadata[plan.source_keys[1]] = (
                plan.source_projection(after) if plan.source_projection is not None else after
            )
            if prepared.source != after:
                raise ValueError(plan.source_error or "运行中父证据变化")
        plan.metadata.update(summary, status="completed")
    except plan.errors as exc:
        if plan.failure_error is None:
            plan.metadata["error"] = str(exc)
        else:
            plan.metadata["error"] = plan.failure_error
            stderr = f"{stderr}\n{exc}"
        if isinstance(exc, RExecutorError):
            stdout, stderr = executor_streams(exc)
    finally:
        finish_stage(
            plan.directory,
            plan.metadata,
            started,
            stdout,
            stderr,
            artifacts=True,
            fingerprints=plan.fingerprint,
        )
    return plan.metadata
