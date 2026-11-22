"""受控分析阶段的证据原语：字节身份、输入指纹与内部 CSV。"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def digest(data: bytes) -> str:
    """同一份实际读取的字节用于身份校验和解析。"""
    return hashlib.sha256(data).hexdigest()


def identity(data: bytes) -> dict[str, Any]:
    """文件内容身份。"""
    return {"bytes": len(data), "sha256": digest(data)}


def fingerprints(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """流式记录原件大小和 SHA256；不把输入复制到共享存储。"""
    result = []
    for path in paths:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(block)
        result.append(
            {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": hasher.hexdigest(),
            }
        )
    return result


def records(data: bytes) -> list[dict[str, str]]:
    """按原顺序读取内部 CSV。"""
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
