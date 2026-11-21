"""ETL 文献批次交接契约：文件名、列集合与读取入口。"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

BATCH_PATTERN = re.compile(r"^articles_\d{8}_\d{6}\.csv$")

EXPORT_FIELDS: tuple[str, ...] = (
    "pmid",
    "title",
    "abstract",
    "keywords",
    "mesh_terms",
    "authors",
    "year",
    "journal",
    "doi",
)

REQUIRED_FIELDS: tuple[str, ...] = ("pmid", "title", "abstract")


class ArticleBatchError(ValueError):
    """文献批次不符合交接契约。"""


def is_batch_name(name: str) -> bool:
    """判断文件名是否为契约批次名。"""
    return BATCH_PATTERN.fullmatch(name) is not None


def discover_batches(output_dir: Path | str) -> list[Path]:
    """按文件名升序列出目录内的契约批次。"""
    return sorted(p for p in Path(output_dir).glob("articles_*.csv") if is_batch_name(p.name))


def latest_batch(output_dir: Path | str) -> Path | None:
    """返回最新的契约批次；无批次时为 None。"""
    batches = discover_batches(output_dir)
    return batches[-1] if batches else None


def _require_columns(header: Sequence[str] | None, required: Sequence[str], path: Path) -> None:
    """校验表头包含全部必需列。"""
    missing = [column for column in required if column not in (header or ())]
    if missing:
        raise ArticleBatchError(f"文献批次缺少列 {missing}: {path.name}")


def validate_header(path: Path | str, *, required: Sequence[str] = REQUIRED_FIELDS) -> None:
    """校验 CSV 表头包含必需列。"""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle), None)
    _require_columns(header, required, Path(path))


def iter_rows(
    path: Path | str, *, required: Sequence[str] = REQUIRED_FIELDS
) -> Iterator[dict[str, str]]:
    """逐行读取批次，读取前校验表头。"""
    path = Path(path)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader.fieldnames, required, path)
        yield from reader
