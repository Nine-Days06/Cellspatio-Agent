"""VerifiedArtifact 接缝的目录、路径、身份与魔数约束。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from src.api.verified_artifact import (
    DIRECTORY_ERROR,
    PATH_ERROR,
    ArtifactError,
    VerifiedArtifact,
    read_verified_artifact,
)

PNG = b"\x89PNG\r\n\x1a\npayload"


def _attempt(directory: Path, name: str, data: bytes, **identity: Any) -> dict[str, Any]:
    return {
        "directory": str(directory),
        "result": {
            "artifacts": {
                name: {
                    "path": str(directory / name),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    **identity,
                }
            }
        },
    }


def test_reads_verified_artifact(tmp_path: Path) -> None:
    image = tmp_path / "pca.png"
    image.write_bytes(PNG)

    artifact = read_verified_artifact(
        attempt=_attempt(tmp_path, image.name, PNG),
        name=image.name,
        identity_error="身份变化",
        expected_directory=tmp_path,
        magic=b"\x89PNG\r\n\x1a\n",
    )

    assert artifact == VerifiedArtifact(path=image, data=PNG)


def test_identity_change_uses_caller_message(tmp_path: Path) -> None:
    image = tmp_path / "umap.png"
    image.write_bytes(PNG)

    with pytest.raises(ArtifactError, match="图身份变化"):
        read_verified_artifact(
            attempt=_attempt(tmp_path, image.name, PNG, bytes=len(PNG) + 1),
            name=image.name,
            identity_error="图身份变化",
        )


def test_rejects_parent_escape(tmp_path: Path) -> None:
    directory = tmp_path / "attempt"
    directory.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    attempt = _attempt(directory, "../outside.png", PNG)
    attempt["result"]["artifacts"]["../outside.png"]["path"] = str(outside)

    with pytest.raises(ArtifactError, match=PATH_ERROR):
        read_verified_artifact(
            attempt=attempt,
            name="../outside.png",
            identity_error="身份变化",
        )


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match=PATH_ERROR):
        read_verified_artifact(
            attempt=_attempt(tmp_path, "missing.png", PNG),
            name="missing.png",
            identity_error="身份变化",
        )


def test_magic_mismatch_uses_caller_message(tmp_path: Path) -> None:
    image = tmp_path / "not-png.bin"
    image.write_bytes(b"not a png")

    with pytest.raises(ArtifactError, match="图身份变化"):
        read_verified_artifact(
            attempt=_attempt(tmp_path, image.name, image.read_bytes()),
            name=image.name,
            identity_error="图身份变化",
            magic=b"\x89PNG\r\n\x1a\n",
        )


def test_expected_directory_mismatch_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "attempt"
    directory.mkdir()
    image = directory / "pca.png"
    image.write_bytes(PNG)

    with pytest.raises(ArtifactError, match=DIRECTORY_ERROR):
        read_verified_artifact(
            attempt=_attempt(directory, image.name, PNG),
            name=image.name,
            identity_error="身份变化",
            expected_directory=tmp_path / "other",
        )


def test_absolute_name_is_rejected(tmp_path: Path) -> None:
    image = tmp_path / "absolute.png"
    image.write_bytes(PNG)

    with pytest.raises(ArtifactError, match=PATH_ERROR):
        read_verified_artifact(
            attempt=_attempt(tmp_path, str(image), PNG),
            name=str(image),
            identity_error="身份变化",
        )


def test_non_flat_name_is_rejected(tmp_path: Path) -> None:
    image = tmp_path / "flat.png"
    image.write_bytes(PNG)

    with pytest.raises(ArtifactError, match=PATH_ERROR):
        read_verified_artifact(
            attempt=_attempt(tmp_path, "./flat.png", PNG),
            name="./flat.png",
            identity_error="身份变化",
        )
