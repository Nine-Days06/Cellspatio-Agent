"""固定细胞列过滤；不执行归一化、聚类或科学解释。"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.analysis.qc_preview import (
    Thresholds,
    _identity,
    check_preview,
    checked_evidence,
    decisions,
)
from src.analysis.r_executor import RExecutorError
from src.analysis.sc_qc import _fingerprints
from src.control.r_script_generator import RScriptGenerator

FILTER_TOOL_VERSION = "1.0.0"


def verified_selection(run: dict[str, Any], root: Path, preview: dict[str, Any]) -> bytes:
    """从同字节 QC 重算全部选择，拒绝只改 selection 或数据库摘要。"""
    check_preview(run, root, preview)
    files = checked_evidence(run, root)
    params = {**preview["params"], "percent_mt_max": Decimal(preview["params"]["percent_mt_max"])}
    rows, summary = decisions(files["cell_qc.csv"], Thresholds(**params))
    if summary != preview["summary"]:
        raise ValueError("预览摘要与 QC 重算不一致")
    path = Path(run["directory"]) / "previews" / preview["preview_id"] / "selection.csv"
    data = path.read_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    expected = [
        {
            "barcode": row["barcode"],
            "keep": str(int(row["keep"])),
            "reasons": ";".join(row["reasons"]),
        }
        for row in rows
    ]
    if reader.fieldnames != ["barcode", "keep", "reasons"] or list(reader) != expected:
        raise ValueError("逐 barcode 选择与 QC 重算不一致")
    if _identity(data) != preview["artifacts"]["selection.csv"]:
        raise ValueError("选择证据已变化")
    return data


def filter_script(input_file: str, directory: Path) -> str:
    """固定稀疏切片与 RDS 往返校验；一列和全零 feature 行也保留。"""
    return (
        RScriptGenerator._single_cell_input_code({"input_file": input_file})
        + f"""
output_dir <- {json.dumps(str(directory), ensure_ascii=False)}
mat <- if (inherits(mat, "sparseMatrix")) mat else methods::as(mat, "dgCMatrix")
selection <- read.csv(file.path(output_dir, "selection.csv"), check.names = FALSE,
                      colClasses = "character", na.strings = character())
qc <- read.csv(file.path(output_dir, "cell_qc.csv"), check.names = FALSE,
               colClasses = "character", na.strings = "NA")
mapping <- read.csv(file.path(output_dir, "features.csv"), check.names = FALSE,
                    colClasses = "character", na.strings = character())
stopifnot(identical(selection$barcode, colnames(mat)),
          identical(qc$barcode, colnames(mat)),
          identical(mapping$unique_name, rownames(mat)),
          all(selection$keep %in% c("0", "1")),
          all(Matrix::colSums(mat) == as.numeric(qc$nCount_RNA)),
          all(Matrix::colSums(mat > 0) == as.numeric(qc$nFeature_RNA)))
keep <- selection$keep == "1"
stopifnot(any(keep))
filtered <- mat[, keep, drop = FALSE]
stopifnot(inherits(filtered, "sparseMatrix"), nrow(filtered) == nrow(mat),
          identical(colnames(filtered), selection$barcode[keep]))
saveRDS(filtered, file.path(output_dir, "filtered_counts.rds"))
restored <- readRDS(file.path(output_dir, "filtered_counts.rds"))
stopifnot(identical(filtered, restored), identical(restored, mat[, keep, drop = FALSE]))
write.csv(data.frame(barcode = colnames(restored)),
          file.path(output_dir, "barcodes.csv"), row.names = FALSE)
writeLines(c(paste("R", getRversion()), paste("Seurat", packageVersion("Seurat")),
             paste("Matrix", packageVersion("Matrix"))), file.path(output_dir, "versions.txt"))
writeLines(c(as.character(nrow(restored)), as.character(ncol(restored)),
             as.character(length(restored@x))), file.path(output_dir, "dimensions.txt"))
"""
    )


def run_sc_filter(
    run: dict[str, Any], preview: dict[str, Any], root: Path, directory: Path, executor: Any
) -> dict[str, Any]:
    """只执行一次固定过滤；失败保留日志，成功仍等待科学审阅。"""
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    metadata: dict[str, Any] = {
        "tool": "sc_filter",
        "tool_version": FILTER_TOOL_VERSION,
        "stage": "filter_review",
        "scientific_status": "unreviewed",
        "status": "failed",
        "timeout_seconds": 300,
        "run_id": run["run_id"],
        "preview_id": preview["preview_id"],
        "binding_sha256": preview["binding_sha256"],
        "inputs": run["evidence"]["inputs"],
    }
    stdout = stderr = ""
    try:
        from src.control.workflow_manager import WorkflowManager

        invalid = WorkflowManager._validate_sc_input(run["evidence"]["input_path"])
        if invalid:
            raise ValueError(invalid["message"])
        selection = verified_selection(run, root, preview)
        files = checked_evidence(run, root)
        (directory / "selection.csv").write_bytes(selection)
        for name in ("features.csv", "cell_qc.csv"):
            (directory / name).write_bytes(files[name])
        script = directory / "script.R"
        script.write_text(
            filter_script(run["evidence"]["input_path"], directory.resolve()), encoding="utf-8"
        )
        completed = executor.execute_script(str(script))
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        metadata["returncode"] = completed.returncode
        if completed.returncode != 0:
            raise ValueError("过滤 R 执行失败")
        for name in (
            "barcodes.csv",
            "dimensions.txt",
            "versions.txt",
            "filtered_counts.rds",
            "features.csv",
            "cell_qc.csv",
            "selection.csv",
            "script.R",
        ):
            path = directory / name
            if path.resolve().parent != directory.resolve() or not path.is_file():
                raise ValueError("过滤产物路径非法或缺失")
        expected = [
            r["barcode"]
            for r in csv.DictReader(io.StringIO(selection.decode()))
            if r["keep"] == "1"
        ]
        with (directory / "barcodes.csv").open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["barcode"] or [r["barcode"] for r in reader] != expected:
                raise ValueError("过滤后的 barcode 与已批准选择不一致")
        dims = [int(x) for x in (directory / "dimensions.txt").read_text().splitlines()]
        if (
            len(dims) != 3
            or dims[:2] != [run["evidence"]["summary"]["features"], len(expected)]
            or dims[2] < 0
        ):
            raise ValueError("过滤矩阵维度不符")
        versions = (directory / "versions.txt").read_text(encoding="utf-8").splitlines()
        if versions != run["evidence"]["summary"]["versions"]:
            raise ValueError("R/Seurat/Matrix 版本与已审 QC 不一致，请重新 QC")
        rds = directory / "filtered_counts.rds"
        if not rds.is_file() or rds.stat().st_size == 0:
            raise ValueError("过滤 RDS 缺失")
        for name in ("features.csv", "cell_qc.csv"):
            if (directory / name).read_bytes() != files[name]:
                raise ValueError("过滤过程中 QC 副本发生变化")
        if (directory / "selection.csv").read_bytes() != selection:
            raise ValueError("过滤过程中选择发生变化")
        if script.read_text(encoding="utf-8") != filter_script(
            run["evidence"]["input_path"], directory.resolve()
        ):
            raise ValueError("过滤固定脚本发生变化")
        verified_selection(run, root, preview)
        metadata.update(
            status="completed",
            cells=len(expected),
            features=dims[0],
            stored_entries=dims[2],
            versions=versions,
        )
    except (RExecutorError, OSError, ValueError, KeyError, StopIteration) as exc:
        metadata["error"] = str(exc)
        if isinstance(exc, RExecutorError):
            stdout, stderr = exc.stdout, exc.stderr
            if isinstance(exc.__cause__, subprocess.TimeoutExpired):
                stdout, stderr = exc.__cause__.stdout or "", exc.__cause__.stderr or ""
    finally:
        for name, text in (("stdout.txt", stdout), ("stderr.txt", stderr)):
            (directory / name).write_text(
                text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text,
                encoding="utf-8",
            )
        metadata["elapsed_seconds"] = time.perf_counter() - started
        metadata["artifacts"] = {
            Path(record["path"]).name: record
            for record in _fingerprints([file for file in directory.iterdir() if file.is_file()])
        }
        (directory / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return metadata
