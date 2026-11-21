"""校验并读取已登记的分析产物；不构造 HTTP 响应。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.analysis.qc_preview import _identity

PATH_ERROR = "产物路径非法或缺失"


class ArtifactError(ValueError):
    """产物目录、路径或身份校验失败。"""


@dataclass(frozen=True)
class VerifiedArtifact:
    """与实际读取字节绑定的已校验产物。"""

    path: Path
    data: bytes


def read_verified_artifact(
    *,
    attempt: dict[str, Any],
    name: str,
    identity_error: str,
    path_error: str = PATH_ERROR,
    expected_directory: str | Path | None = None,
    magic: bytes | None = None,
) -> VerifiedArtifact:
    """读取 attempt 目录下的命名产物，并核对登记的身份和可选魔数。"""
    try:
        directory = Path(attempt["directory"]).resolve()
    except (KeyError, OSError, TypeError) as exc:
        raise ArtifactError(path_error) from exc
    if not directory.is_dir():
        raise ArtifactError(path_error)
    if expected_directory is not None:
        try:
            expected = Path(expected_directory).resolve()
        except (OSError, TypeError) as exc:
            raise ArtifactError(path_error) from exc
        if directory != expected:
            raise ArtifactError(path_error)

    relative = Path(name)
    if not name or relative.is_absolute() or relative.anchor or relative.name != name:
        raise ArtifactError(path_error)
    path = directory / relative
    try:
        if path.resolve().parent != directory:
            raise ArtifactError(path_error)
    except (OSError, RuntimeError) as exc:
        raise ArtifactError(path_error) from exc
    if not path.is_file():
        raise ArtifactError(path_error)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ArtifactError(path_error) from exc

    try:
        registered = attempt["result"]["artifacts"][name]
    except (KeyError, TypeError) as exc:
        raise ArtifactError(identity_error) from exc
    if {"path": str(path), **_identity(data)} != registered:
        raise ArtifactError(identity_error)
    if magic is not None and not data.startswith(magic):
        raise ArtifactError(identity_error)
    return VerifiedArtifact(path=path, data=data)
