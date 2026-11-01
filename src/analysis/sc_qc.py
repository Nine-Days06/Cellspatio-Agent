"""固定的人类单细胞过滤前 QC；证据只写本地，不执行下游分析。"""

import base64
import csv
import hashlib
import json
import math
import statistics
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from src.analysis.r_executor import RExecutorError
from src.control.r_script_generator import RScriptGenerator
from src.data.tenx import _open_text, tenx_files

QC_TOOL_VERSION = "1.0.0"


def qc_script(input_file: str, directory: Path) -> str:
    """复用稀疏读取块，以原始 symbol 计算线粒体 counts，不建过滤后的对象。"""
    output_dir = json.dumps(str(directory), ensure_ascii=False)
    return (
        RScriptGenerator._single_cell_input_code({"input_file": input_file})
        + f"""
output_dir <- {output_dir}
mapping <- if (dir.exists(input_file)) {{
    data.frame(feature_id = features[rna, 1], symbol = features[rna, 2],
               unique_name = rownames(mat), stringsAsFactors = FALSE)
}} else {{
    data.frame(feature_id = rownames(mat), symbol = rownames(mat),
               unique_name = rownames(mat), stringsAsFactors = FALSE)
}}
mt <- grepl("^MT-", mapping$symbol)
mt_known <- any(mt)
total <- Matrix::colSums(mat)
detected <- Matrix::colSums(mat > 0)
percent_mt <- rep(NA_real_, ncol(mat))
reason <- rep(if (mt_known) "" else "mt_symbol_unresolved", ncol(mat))
if (mt_known) {{
    positive <- total > 0
    percent_mt[positive] <- Matrix::colSums(mat[mt, , drop = FALSE])[positive] /
                            total[positive] * 100
}}
reason[total == 0] <- if (mt_known) "zero_counts" else "zero_counts;mt_symbol_unresolved"
qc <- data.frame(barcode = colnames(mat), nCount_RNA = total, nFeature_RNA = detected,
                 percent_mt = percent_mt, missing_reason = reason,
                 stringsAsFactors = FALSE)
write.csv(mapping, file.path(output_dir, "features.csv"), row.names = FALSE, na = "NA")
write.csv(qc, file.path(output_dir, "cell_qc.csv"), row.names = FALSE, na = "NA")
writeLines(c(paste("R", getRversion()),
             paste("Seurat", packageVersion("Seurat")),
             paste("Matrix", packageVersion("Matrix"))), file.path(output_dir, "versions.txt"))
png(file.path(output_dir, "qc.png"), width = 1600, height = 1000, res = 140)
par(mfrow = c(2, 3))
hist(qc$nCount_RNA, main = "RNA counts (before filtering)", xlab = "nCount_RNA")
hist(qc$nFeature_RNA, main = "Detected RNA features", xlab = "nFeature_RNA")
if (any(is.finite(percent_mt))) {{
    hist(percent_mt[is.finite(percent_mt)], main = "Human MT- counts / RNA counts", xlab = "% MT")
}} else {{
    plot.new(); title("MT percentage unavailable")
}}
plot(qc$nCount_RNA, qc$nFeature_RNA, pch = 16, cex = 0.35,
     xlab = "nCount_RNA", ylab = "nFeature_RNA", main = "All RNA barcodes (unfiltered)")
for (metric in c("nCount_RNA", "nFeature_RNA")) {{
    if (any(is.finite(percent_mt))) {{
        plot(qc[[metric]], percent_mt, pch = 16, cex = 0.35,
             xlab = metric, ylab = "% MT", main = "MT percentage (defined barcodes only)")
    }} else {{
        plot.new(); title("MT percentage unavailable")
    }}
}}
dev.off()
"""
    )


def _fingerprints(paths: list[Path]) -> list[dict[str, Any]]:
    """流式记录原件大小和 SHA256；不把输入复制到共享存储。"""
    records = []
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        records.append(
            {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
            }
        )
    return records


def _source_labels(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """读取输入标识以验证全量 barcode、RNA features 和原 symbol 的对应关系。"""
    if path.is_dir():
        files = tenx_files(path)
        with _open_text(files["barcodes"]) as handle:
            barcodes = [row[0] for row in csv.reader(handle, delimiter="\t")]
        with _open_text(files["features"]) as handle:
            features = [
                (row[0], row[1])
                for row in csv.reader(handle, delimiter="\t")
                if len(row) == 2 or row[2] == "Gene Expression"
            ]
    else:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = csv.reader(handle)
            barcodes = next(rows)[1:]
            features = [(row[0], row[0]) for row in rows]
    return barcodes, features


def _read_evidence(directory: Path, path: Path) -> dict[str, Any]:
    """拒绝缺失、截断、重复、错位或非有限的 QC 产物。"""
    barcodes, features = _source_labels(path)
    with (directory / "features.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["feature_id", "symbol", "unique_name"]:
            raise ValueError("QC feature 映射列不符")
        mapping = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in mapping):
        raise ValueError("QC feature 映射行截断或列数错误")
    if (
        [(row["feature_id"], row["symbol"]) for row in mapping] != features
        or len({row["unique_name"] for row in mapping}) != len(features)
        or any(not row["unique_name"] for row in mapping)
    ):
        raise ValueError("QC feature 映射与原输入不一致")
    mt_known = any(symbol.startswith("MT-") for _, symbol in features)
    with (directory / "cell_qc.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != [
            "barcode",
            "nCount_RNA",
            "nFeature_RNA",
            "percent_mt",
            "missing_reason",
        ]:
            raise ValueError("QC 每细胞指标列不符")
        cells = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in cells):
        raise ValueError("QC 每细胞指标行截断或列数错误")
    if [row["barcode"] for row in cells] != barcodes or len(set(barcodes)) != len(barcodes):
        raise ValueError("QC 必须保留全部 barcode 且顺序不变")
    values: dict[str, list[float]] = {
        name: [] for name in ("nCount_RNA", "nFeature_RNA", "percent_mt")
    }
    zero_cells = 0
    for row in cells:
        count, detected = float(row["nCount_RNA"]), float(row["nFeature_RNA"])
        if (
            any(not math.isfinite(v) or v < 0 or v != int(v) for v in (count, detected))
            or detected > len(features)
            or detected > count
            or (count == 0) != (detected == 0)
        ):
            raise ValueError("QC counts/features 指标无效")
        zero_cells += count == 0
        reason = []
        if count == 0:
            reason.append("zero_counts")
        if not mt_known:
            reason.append("mt_symbol_unresolved")
        if row["missing_reason"] != ";".join(reason):
            raise ValueError("QC 缺失原因与输入不一致")
        if reason:
            if row["percent_mt"] != "NA":
                raise ValueError("无法计算的线粒体比例必须为 NA")
        else:
            percent = float(row["percent_mt"])
            if not math.isfinite(percent) or not 0 <= percent <= 100:
                raise ValueError("QC 线粒体比例无效")
            values["percent_mt"].append(percent)
        values["nCount_RNA"].append(count)
        values["nFeature_RNA"].append(detected)
    versions = (directory / "versions.txt").read_text(encoding="utf-8").splitlines()
    if len(versions) != 3 or any(
        not line.startswith(name + " ")
        for line, name in zip(versions, ("R", "Seurat", "Matrix"), strict=True)
    ):
        raise ValueError("QC 版本证据缺失")
    if not (directory / "qc.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("QC 图表缺失或格式错误")
    warnings = []
    if not mt_known:
        warnings.append("未匹配原始人类 MT- symbol，percent.mt 为 NA；不可据此过滤。")
    if zero_cells:
        warnings.append(f"{zero_cells} 个 barcode 的 RNA counts 为零，percent.mt 为 NA。")
    return {
        "tool_version": QC_TOOL_VERSION,
        "stage": "qc_review",
        "scientific_status": "unreviewed",
        "filtered": False,
        "cells": len(cells),
        "features": len(features),
        "zero_count_cells": zero_cells,
        "mt_symbol_resolved": mt_known,
        "warnings": warnings,
        "versions": versions,
        "metrics": {
            key: {
                "available": len(data),
                "min": min(data) if data else None,
                "median": statistics.median(data) if data else None,
                "max": max(data) if data else None,
            }
            for key, data in values.items()
        },
    }


def run_sc_qc(
    input_file: str, executor: Any, output_root: Path, run_id: str | None = None
) -> dict[str, Any]:
    """运行一次固定 QC，成功停在审阅；失败保留原脚本、日志和元数据。"""
    directory = output_root / (run_id or f"qc-{uuid.uuid4().hex}")
    directory.mkdir(parents=True, exist_ok=False)
    path = Path(input_file).resolve()
    script_path = directory / "script.R"
    script_path.write_text(qc_script(str(path), directory.resolve()), encoding="utf-8")
    metadata: dict[str, Any] = {
        "tool": "sc_qc",
        "tool_version": QC_TOOL_VERSION,
        "stage": "qc_review",
        "scientific_status": "unreviewed",
        "input_path": str(path),
        "timeout_seconds": 300,
        "status": "error",
    }
    started = time.perf_counter()
    stdout = stderr = ""
    result: dict[str, Any] = {
        "status": "error",
        "type": "analysis",
        "analysis_type": "single_cell",
        "stage": "qc_review",
        "message": "QC 未完成；本地证据已保留，未执行下游分析。",
    }
    try:
        paths = list(tenx_files(path).values()) if path.is_dir() else [path]
        metadata["inputs_before"] = _fingerprints(paths)
        completed = executor.execute_script(str(script_path))
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        metadata["returncode"] = completed.returncode
        if completed.returncode != 0:
            raise ValueError("QC R 执行失败")
        metadata["inputs_after"] = _fingerprints(paths)
        if metadata["inputs_before"] != metadata["inputs_after"]:
            raise ValueError("QC 运行期间原输入发生变化")
        summary = _read_evidence(directory, path)
        current_paths = list(tenx_files(path).values()) if path.is_dir() else [path]
        metadata["inputs_after"] = _fingerprints(current_paths)
        if metadata["inputs_before"] != metadata["inputs_after"]:
            raise ValueError("QC 校验期间原输入发生变化")
        (directory / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        metadata["status"] = "needs_confirmation"
        result.update(
            status="needs_confirmation",
            message="过滤前 QC 证据已生成，等待科学审阅；未过滤、聚类或注释。",
        )
        result["results"] = {
            "qc": summary,
            "charts": [
                {
                    "type": "image",
                    "image_b64": base64.b64encode((directory / "qc.png").read_bytes()).decode(
                        "ascii"
                    ),
                }
            ],
        }
    except (RExecutorError, OSError, ValueError, KeyError, StopIteration) as exc:
        metadata["error"] = str(exc)
        if isinstance(exc, RExecutorError):
            stdout, stderr = exc.stdout, exc.stderr
            if isinstance(exc.__cause__, subprocess.TimeoutExpired):
                stdout, stderr = exc.__cause__.stdout or "", exc.__cause__.stderr or ""
    finally:
        if "inputs_before" in metadata and "inputs_after" not in metadata:
            try:
                metadata["inputs_after"] = _fingerprints(paths)
            except OSError as exc:
                metadata["input_recheck_error"] = str(exc)
        for name, text in (("stdout.txt", stdout), ("stderr.txt", stderr)):
            (directory / name).write_text(
                text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text,
                encoding="utf-8",
            )
        metadata["elapsed_seconds"] = time.perf_counter() - started
        (directory / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    artifacts = {file.name: str(file.resolve()) for file in directory.iterdir() if file.is_file()}
    result.setdefault("results", {})["qc_artifacts"] = artifacts
    return result
