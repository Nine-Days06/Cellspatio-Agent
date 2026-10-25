"""10x MTX 三件套识别与流式 counts 校验，不加载稠密矩阵。"""

import csv
import gzip
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import TextIO


def tenx_files(directory: Path) -> dict[str, Path]:
    """确定唯一三件套路径，兼容旧版 genes.tsv 与独立 gzip 压缩。"""
    files = {}
    for key, names in (
        ("matrix", ("matrix.mtx",)),
        ("barcodes", ("barcodes.tsv",)),
        ("features", ("features.tsv", "genes.tsv")),
    ):
        candidates = [
            directory / (name + suffix)
            for name in names
            for suffix in ("", ".gz")
            if (directory / (name + suffix)).is_file()
        ]
        if len(candidates) != 1:
            raise ValueError(f"10x 目录必须有且只有一个 {key} 文件（支持 .gz）")
        files[key] = candidates[0]
    return files


def _open_text(path: Path) -> TextIO:
    """以相同编码流式打开普通或 gzip 文本。"""
    opener = gzip.open if path.suffix == ".gz" else open
    return opener(path, "rt", encoding="utf-8-sig", newline="")


def validate_tenx(directory: Path) -> None:
    """校验标识、RNA 类型、MatrixMarket 结构及每条坐标计数。"""
    files = tenx_files(directory)
    sizes = {}
    for key in ("barcodes", "features"):
        identifiers: set[str] = set()
        rna_features = 0
        legacy = files[key].name.startswith("genes.tsv")
        with _open_text(files[key]) as handle:
            for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
                expected = 1 if key == "barcodes" else (2 if legacy else 3)
                if len(row) != expected or not all(value.strip() for value in row):
                    raise ValueError(f"{key} 列数不符或包含空标识")
                identifier = row[0].strip()
                if identifier in identifiers:
                    raise ValueError(f"{key} 第一列标识必须唯一")
                identifiers.add(identifier)
                if key == "features" and (legacy or row[2] == "Gene Expression"):
                    rna_features += 1
        sizes[key] = len(identifiers)
        if not identifiers or (key == "features" and not rna_features):
            raise ValueError(f"{key} 为空或没有 Gene Expression 数据")

    with _open_text(files["matrix"]) as handle:
        header = handle.readline().strip().lower().split()
        if (
            len(header) != 5
            or header[:3] != ["%%matrixmarket", "matrix", "coordinate"]
            or header[3] not in ("integer", "real")
            or header[4] != "general"
        ):
            raise ValueError("MatrixMarket 必须是 coordinate integer/real general 原始 counts")
        lines = (
            line.strip() for line in handle if line.strip() and not line.lstrip().startswith("%")
        )
        dimensions = next(lines, "").split()
        if len(dimensions) != 3 or any(not re.fullmatch(r"[0-9]+", n) for n in dimensions):
            raise ValueError("MatrixMarket 维度行无效")
        rows, columns, entries = map(int, dimensions)
        if rows != sizes["features"] or columns != sizes["barcodes"]:
            raise ValueError("矩阵维度必须与 features/barcodes 行数一致")
        count_pattern = (
            r"[+-]?[0-9]+"
            if header[3] == "integer"
            else r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
        )
        seen_entries = 0
        for line in lines:
            fields = line.split()
            if (
                len(fields) != 3
                or any(not re.fullmatch(r"[+-]?[0-9]+", n) for n in fields[:2])
                or not re.fullmatch(count_pattern, fields[2])
            ):
                raise ValueError("MatrixMarket 坐标和 counts 必须为整数")
            row, column = map(int, fields[:2])
            count = Decimal(fields[2])
            if not (1 <= row <= rows and 1 <= column <= columns):
                raise ValueError("MatrixMarket 坐标越界")
            if (
                not count.is_finite()
                or count < 0
                or count != count.to_integral_value()
                or not math.isfinite(float(count))
            ):
                raise ValueError("counts 必须是有限的非负整数")
            seen_entries += 1
            if seen_entries > entries:
                raise ValueError("MatrixMarket 条目数超过声明值")
        if seen_entries != entries:
            raise ValueError("MatrixMarket 条目数与声明值不符")
