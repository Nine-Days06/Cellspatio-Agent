"""固定归一化/PCA；只消费登记的过滤 counts，停在待复核阶段。"""

from __future__ import annotations

import csv
import io
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any

from src.analysis.qc_preview import _digest, checked_evidence
from src.analysis.r_executor import RExecutorError
from src.analysis.sc_filter import FILTER_TOOL_VERSION, filter_script, verified_selection
from src.analysis.sc_qc import _fingerprints

PCA_TOOL_VERSION = "1.0.0"
PROTOCOL = {
    "schema": "sc-pca/1.0.0",
    "object": {"assay": "RNA", "min.cells": 0, "min.features": 0},
    "normalize": {"normalization.method": "LogNormalize", "scale.factor": 10000, "margin": 1},
    "variable": {
        "selection.method": "vst",
        "nfeatures": 2000,
        "loess.span": 0.3,
        "clip.max": "auto",
    },
    "scale": {
        "features": "selected_variable_only",
        "vars.to.regress": None,
        "do.center": True,
        "do.scale": True,
        "scale.max": 10,
        "model.use": "linear",
        "block.size": 1000,
        "min.cells.to.block": 3000,
    },
    "pca": {
        "rev.pca": False,
        "weight.by.var": True,
        "approx": True,
        "seed.use": 42,
        "npcs_max": 30,
        "rule": "min(30, cells - 1, usable_scaled_variable_features - 1)",
        "minimum_cells": 3,
        "minimum_usable_features": 3,
    },
    "packages": {
        "R": "4.6.1",
        "Seurat": "5.6.0",
        "SeuratObject": "5.4.0",
        "Matrix": "1.7.5",
        "irlba": "2.4.1",
    },
}
PARENT_FILES = (
    "filtered_counts.rds",
    "barcodes.csv",
    "features.csv",
    "cell_qc.csv",
    "selection.csv",
    "dimensions.txt",
    "versions.txt",
    "script.R",
)
OUTPUT_FILES = (
    "seurat_pca.rds",
    "feature_mapping.csv",
    "variable_features.csv",
    "pca_embeddings.csv",
    "pca_loadings.csv",
    "pca_stdev.csv",
    "pca_diagnostics.png",
    "summary.json",
    "versions.txt",
    "session_info.txt",
    "script.R",
)


def _records(data: bytes) -> list[dict[str, str]]:
    """按原顺序读取内部 CSV。"""
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def checked_parent(run: dict[str, Any], parent: dict[str, Any], root: Path) -> dict[str, Any]:
    """核对数据库结果、受控目录、源证据及当前批准；不启动 R。"""
    if parent["status"] != "completed" or parent["tool_version"] != FILTER_TOOL_VERSION:
        raise ValueError("父过滤未完成或工具版本不符")
    preview = next((p for p in run["previews"] if p["preview_id"] == parent["preview_id"]), None)
    if (
        preview is None
        or preview["status"] != "approved"
        or run["active_preview_id"] != parent["preview_id"]
        or parent["binding_sha256"] != preview["binding_sha256"]
    ):
        raise ValueError("父过滤不再对应当前已批准选择")
    selection = verified_selection(run, root, preview)
    qc = checked_evidence(run, root)
    directory = Path(parent["directory"]).resolve()
    if directory != root.parent / "sc_filter" / parent["attempt_id"]:
        raise ValueError("父过滤目录非法")
    meta_path = directory / "metadata.json"
    if meta_path.resolve().parent != directory:
        raise ValueError("父过滤 metadata 路径非法")
    meta = json.loads(meta_path.read_bytes())
    result = parent["result"]
    if meta != result or any(
        meta.get(k) != v
        for k, v in {
            "tool": "sc_filter",
            "tool_version": FILTER_TOOL_VERSION,
            "status": "completed",
            "stage": "filter_review",
            "scientific_status": "unreviewed",
            "returncode": 0,
            "run_id": run["run_id"],
            "preview_id": preview["preview_id"],
            "binding_sha256": preview["binding_sha256"],
            "inputs": run["evidence"]["inputs"],
        }.items()
    ):
        raise ValueError("父过滤 metadata 与权威结果不一致")
    paths = [directory / name for name in PARENT_FILES]
    if any(p.resolve().parent != directory or not p.is_file() for p in paths):
        raise ValueError("父过滤文件缺失或路径非法")
    identities = {Path(r["path"]).name: r for r in _fingerprints(paths)}
    if any(identities[n] != result["artifacts"].get(n) for n in PARENT_FILES):
        raise ValueError("父过滤文件身份已变化")
    for name in ("features.csv", "cell_qc.csv"):
        if (directory / name).read_bytes() != qc[name]:
            raise ValueError("父过滤 QC 副本不一致")
    if (directory / "selection.csv").read_bytes() != selection:
        raise ValueError("父过滤选择副本不一致")
    if (directory / "script.R").read_text(encoding="utf-8") != filter_script(
        run["evidence"]["input_path"], directory
    ):
        raise ValueError("父过滤固定脚本不符")
    kept = [r["barcode"] for r in _records(selection) if r["keep"] == "1"]
    barcodes = _records((directory / "barcodes.csv").read_bytes())
    dimensions = [int(x) for x in (directory / "dimensions.txt").read_text().splitlines()]
    if (
        barcodes != [{"barcode": b} for b in kept]
        or dimensions != [result["features"], result["cells"], result["stored_entries"]]
        or len(kept) != result["cells"]
        or result["features"] != run["evidence"]["summary"]["features"]
        or (directory / "versions.txt").read_text().splitlines() != result["versions"]
        or result["versions"] != run["evidence"]["summary"]["versions"]
    ):
        raise ValueError("父过滤维度、barcode 或版本不一致")
    payload = {
        "run_id": run["run_id"],
        "preview_id": parent["preview_id"],
        "filter_attempt_id": parent["attempt_id"],
        "filter_tool_version": parent["tool_version"],
        "selection_binding": parent["binding_sha256"],
        "evidence": run["evidence"],
        "parent_files": identities,
        "protocol": PROTOCOL,
        "tool_version": PCA_TOOL_VERSION,
    }
    return {
        "binding_sha256": _digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()),
        "source": payload,
        "cells": result["cells"],
        "features": result["features"],
    }


def pca_script(parent_directory: Path, directory: Path) -> str:
    """固定 Seurat 协议、保序 counts 与 RDS 往返校验；无下游步骤。"""
    return f"""
suppressPackageStartupMessages(library(Seurat))
suppressPackageStartupMessages(library(Matrix))
suppressPackageStartupMessages(library(jsonlite))
parent_dir <- {json.dumps(parent_directory.as_posix())}
output_dir <- {json.dumps(directory.as_posix())}
required <- c(R="4.6.1", Seurat="5.6.0", SeuratObject="5.4.0", Matrix="1.7.5", irlba="2.4.1")
versions <- c(R=as.character(getRversion()), sapply(names(required)[-1], function(p) as.character(packageVersion(p))))
stopifnot(identical(unname(versions), unname(required)))
writeLines(paste(names(versions), versions), file.path(output_dir, "versions.txt"))
writeLines(capture.output(sessionInfo()), file.path(output_dir, "session_info.txt"))
read_table <- function(name) read.csv(file.path(parent_dir, name), check.names=FALSE,
                                    colClasses="character", na.strings=character())
counts <- readRDS(file.path(parent_dir, "filtered_counts.rds"))
mapping <- read_table("features.csv")
barcodes <- read_table("barcodes.csv")$barcode
selection <- read_table("selection.csv")
qc <- read_table("cell_qc.csv")
stopifnot(inherits(counts, "dgCMatrix"), ncol(counts) >= 3L,
          all(is.finite(counts@x)), all(counts@x >= 0), all(counts@x == floor(counts@x)),
          identical(mapping$unique_name, rownames(counts)), identical(barcodes, colnames(counts)),
          identical(selection$barcode, qc$barcode), identical(selection$barcode[selection$keep == "1"], barcodes),
          all(Matrix::colSums(counts) > 0),
          all(Matrix::colSums(counts) == as.numeric(qc$nCount_RNA[selection$keep == "1"])),
          all(Matrix::colSums(counts > 0) == as.numeric(qc$nFeature_RNA[selection$keep == "1"])))
mapping$parent_rowname <- rownames(counts)
mapping$seurat_feature_key <- sprintf("F%08d", seq_len(nrow(counts)))
internal <- counts
rownames(internal) <- mapping$seurat_feature_key
obj <- CreateSeuratObject(counts=internal, assay="RNA", min.cells=0, min.features=0)
obj@misc$feature_mapping <- mapping
obj <- NormalizeData(obj, assay="RNA", normalization.method="LogNormalize", scale.factor=10000,
                     margin=1, verbose=FALSE)
obj <- FindVariableFeatures(obj, assay="RNA", selection.method="vst", nfeatures=2000,
                            loess.span=0.3, clip.max="auto", verbose=FALSE)
selected <- VariableFeatures(obj, assay="RNA")
stopifnot(length(selected) >= 3L, !anyDuplicated(selected), all(selected %in% rownames(internal)))
obj <- ScaleData(obj, assay="RNA", features=selected, vars.to.regress=NULL,
                 do.center=TRUE, do.scale=TRUE, scale.max=10, model.use="linear",
                 block.size=1000, min.cells.to.block=3000, verbose=FALSE)
scaled <- LayerData(obj, assay="RNA", layer="scale.data")
finite <- apply(scaled, 1L, function(x) all(is.finite(x)))
stopifnot(all(finite))
positive <- apply(scaled, 1L, var) > 0
usable <- selected[selected %in% rownames(scaled)[finite & positive]]
stopifnot(length(usable) >= 3L)
npcs <- as.integer(min(30L, ncol(counts)-1L, length(usable)-1L))
obj <- RunPCA(obj, assay="RNA", features=usable, npcs=npcs, rev.pca=FALSE,
              weight.by.var=TRUE, approx=TRUE, seed.use=42, verbose=FALSE)
embedding <- Embeddings(obj, "pca")
loadings <- Loadings(obj, "pca")
stdev <- Stdev(obj, "pca")
stopifnot(identical(rownames(embedding), barcodes), identical(rownames(loadings), usable),
          ncol(embedding) == npcs, ncol(loadings) == npcs, length(stdev) == npcs,
          all(is.finite(embedding)), all(is.finite(loadings)), all(is.finite(stdev)), all(stdev > 0))
verify <- function(object) {{
    restored <- LayerData(object, assay="RNA", layer="counts")
    stopifnot(identical(rownames(restored), mapping$seurat_feature_key),
              identical(colnames(restored), colnames(counts)))
    rownames(restored) <- mapping$parent_rowname
    stopifnot(identical(restored, counts), identical(object@misc$feature_mapping, mapping),
              identical(VariableFeatures(object), selected),
              identical(LayerData(object, assay="RNA", layer="scale.data"), scaled),
              identical(Embeddings(object, "pca"), embedding), identical(Loadings(object, "pca"), loadings),
              identical(Stdev(object, "pca"), stdev))
    normalized <- LayerData(object, assay="RNA", layer="data")
    expected <- internal
    expected@x <- log1p(expected@x * rep(10000 / Matrix::colSums(internal), diff(internal@p)))
    stopifnot(identical(dimnames(normalized), dimnames(internal)),
              all(is.finite(normalized@x)), all(normalized@x >= 0),
              max(abs(normalized - expected)) < 1e-10)
}}
verify(obj)
saveRDS(obj, file.path(output_dir, "seurat_pca.rds"))
verify(readRDS(file.path(output_dir, "seurat_pca.rds")))
write.csv(mapping, file.path(output_dir, "feature_mapping.csv"), row.names=FALSE)
variables <- mapping[match(selected, mapping$seurat_feature_key), , drop=FALSE]
variables$usable <- selected %in% usable
variables$excluded_reason <- ifelse(variables$usable, "", "missing_nonfinite_or_zero_variance_scaled")
write.csv(variables, file.path(output_dir, "variable_features.csv"), row.names=FALSE)
write.csv(data.frame(barcode=rownames(embedding), embedding, check.names=FALSE),
          file.path(output_dir, "pca_embeddings.csv"), row.names=FALSE)
write.csv(data.frame(seurat_feature_key=rownames(loadings), loadings, check.names=FALSE),
          file.path(output_dir, "pca_loadings.csv"), row.names=FALSE)
write.csv(data.frame(pc=paste0("PC_", seq_len(npcs)), stdev=stdev),
          file.path(output_dir, "pca_stdev.csv"), row.names=FALSE)
png(file.path(output_dir, "pca_diagnostics.png"), width=1200, height=600)
par(mfrow=c(1,2))
plot(seq_len(npcs), stdev, type="b", xlab="PC", ylab="Standard deviation", main="PCA diagnostic (unreviewed)")
plot(embedding[,1], embedding[,2], pch=16, cex=0.4, xlab="PC1", ylab="PC2", main="No clusters or annotation")
dev.off()
write_json(list(cells=ncol(counts), features=nrow(counts), selected_features=length(selected),
                usable_features=length(usable), actual_pcs=npcs, requested_pcs_max=30L,
                pc_reduction_reason=if(npcs < 30L) "cell_or_usable_feature_limit" else "none",
                counts_unchanged=TRUE), file.path(output_dir, "summary.json"), auto_unbox=TRUE)
"""


def _check_outputs(directory: Path, parent_directory: Path) -> dict[str, Any]:
    """独立 Python 结构校验；不将伪造/截断文件包装成 completed。"""
    paths = [directory / name for name in OUTPUT_FILES]
    if any(
        p.resolve().parent != directory or not p.is_file() or p.stat().st_size == 0 for p in paths
    ):
        raise ValueError("PCA 产物缺失、空或路径非法")
    summary = json.loads((directory / "summary.json").read_bytes())
    mapping = _records((directory / "feature_mapping.csv").read_bytes())
    parents = _records((parent_directory / "features.csv").read_bytes())
    if len(mapping) != len(parents) or any(
        row
        != {
            **original,
            "parent_rowname": original["unique_name"],
            "seurat_feature_key": f"F{i:08d}",
        }
        for i, (row, original) in enumerate(zip(mapping, parents, strict=True), 1)
    ):
        raise ValueError("PCA feature 映射不一致")
    variables = _records((directory / "variable_features.csv").read_bytes())
    keys = [r["seurat_feature_key"] for r in mapping]
    selected = [r["seurat_feature_key"] for r in variables]
    usable = [r["seurat_feature_key"] for r in variables if r["usable"] == "TRUE"]
    if (
        len(set(selected)) != len(selected)
        or not set(selected) <= set(keys)
        or len(selected) > 2000
        or len(usable) < 3
        or any(r["usable"] not in ("TRUE", "FALSE") for r in variables)
    ):
        raise ValueError("PCA 变异特征非法")
    originals = {row["seurat_feature_key"]: row for row in mapping}
    if any(
        row
        != {
            **originals[row["seurat_feature_key"]],
            "usable": row["usable"],
            "excluded_reason": ""
            if row["usable"] == "TRUE"
            else "missing_nonfinite_or_zero_variance_scaled",
        }
        for row in variables
    ):
        raise ValueError("PCA 变异特征映射不一致")
    expected_pcs = min(30, summary["cells"] - 1, len(usable) - 1)
    dims = [int(x) for x in (parent_directory / "dimensions.txt").read_text().splitlines()]
    if (
        summary["cells"] < 3
        or [summary["features"], summary["cells"]] != dims[:2]
        or summary["selected_features"] != len(selected)
        or summary["usable_features"] != len(usable)
        or summary["actual_pcs"] != expected_pcs
        or summary["requested_pcs_max"] != 30
        or summary["counts_unchanged"] is not True
        or summary["pc_reduction_reason"]
        != ("cell_or_usable_feature_limit" if expected_pcs < 30 else "none")
    ):
        raise ValueError("PCA 汇总与批准规则不一致")
    for filename, key, expected in (
        (
            "pca_embeddings.csv",
            "barcode",
            [r["barcode"] for r in _records((parent_directory / "barcodes.csv").read_bytes())],
        ),
        ("pca_loadings.csv", "seurat_feature_key", usable),
    ):
        with (directory / filename).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != [key, *[f"PC_{i}" for i in range(1, expected_pcs + 1)]]:
                raise ValueError("PCA 数值列数不一致")
            rows = list(reader)
        if [r[key] for r in rows] != expected or any(
            not math.isfinite(float(v)) for r in rows for k, v in r.items() if k != key
        ):
            raise ValueError("PCA 数值或顺序非法")
    stdev = _records((directory / "pca_stdev.csv").read_bytes())
    if [r["pc"] for r in stdev] != [f"PC_{i}" for i in range(1, expected_pcs + 1)] or any(
        not math.isfinite(float(r["stdev"])) or float(r["stdev"]) <= 0 for r in stdev
    ):
        raise ValueError("PCA 标准差非法")
    if (directory / "pca_diagnostics.png").read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PCA 诊断图非法")
    versions = (directory / "versions.txt").read_text().splitlines()
    if versions != [f"{name} {version}" for name, version in PROTOCOL["packages"].items()]:
        raise ValueError("PCA 包版本不符")
    return {**summary, "versions": versions}


def checked_completed_pca(
    run: dict[str, Any], parent: dict[str, Any], attempt: dict[str, Any], root: Path
) -> dict[str, Any]:
    """只读复用第四片结构校验及登记身份；不启动 R 或改写证据。"""
    source = checked_parent(run, parent, root)
    if (
        attempt["status"] != "completed"
        or attempt["filter_attempt_id"] != parent["attempt_id"]
        or attempt["tool_version"] != PCA_TOOL_VERSION
        or json.loads(attempt["protocol"]) != PROTOCOL
        or attempt["binding_sha256"] != source["binding_sha256"]
    ):
        raise ValueError("PCA 未完成或当前父绑定、协议、版本不符")
    directory = Path(attempt["directory"]).resolve()
    if directory != root.parent / "sc_pca" / attempt["attempt_id"]:
        raise ValueError("PCA 目录非法")
    meta_path = directory / "metadata.json"
    if meta_path.resolve().parent != directory:
        raise ValueError("PCA metadata 路径非法")
    meta = json.loads(meta_path.read_bytes())
    if meta != attempt["result"] or any(
        meta.get(k) != v
        for k, v in {
            "tool": "sc_pca",
            "tool_version": PCA_TOOL_VERSION,
            "protocol": PROTOCOL,
            "run_id": run["run_id"],
            "filter_attempt_id": parent["attempt_id"],
            "binding_sha256": source["binding_sha256"],
            "status": "completed",
            "stage": "pca_review",
            "scientific_status": "unreviewed",
            "returncode": 0,
            "source_before": source["source"],
            "source_after": source["source"],
        }.items()
    ):
        raise ValueError("PCA metadata 与权威结果不一致")
    paths = [directory / name for name in meta["artifacts"]]
    if any(p.resolve().parent != directory or not p.is_file() for p in paths):
        raise ValueError("PCA 登记文件缺失或路径非法")
    identities = {Path(r["path"]).name: r for r in _fingerprints(paths)}
    if identities != meta["artifacts"] or not set(OUTPUT_FILES) <= identities.keys():
        raise ValueError("PCA 文件身份已变化")
    summary = _check_outputs(directory, Path(parent["directory"]).resolve())
    if any(meta.get(k) != v for k, v in summary.items()):
        raise ValueError("PCA 汇总与产物不一致")
    if (directory / "script.R").read_text(encoding="utf-8") != pca_script(
        Path(parent["directory"]).resolve(), directory
    ):
        raise ValueError("PCA 固定脚本不符")
    return {
        "actual_pcs": summary["actual_pcs"],
        "stdev": [
            {"pc": r["pc"], "stdev": float(r["stdev"])}
            for r in _records((directory / "pca_stdev.csv").read_bytes())
        ],
        "artifacts": identities,
    }


def run_sc_pca(
    run: dict[str, Any],
    parent: dict[str, Any],
    root: Path,
    directory: Path,
    binding: str,
    executor: Any,
) -> dict[str, Any]:
    """事务外执行一次；任何失败保留证据，绝不追加步骤或自动重试。"""
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    metadata: dict[str, Any] = {
        "tool": "sc_pca",
        "tool_version": PCA_TOOL_VERSION,
        "protocol": PROTOCOL,
        "run_id": run["run_id"],
        "filter_attempt_id": parent["attempt_id"],
        "binding_sha256": binding,
        "stage": "pca_review",
        "scientific_status": "unreviewed",
        "status": "failed",
        "timeout_seconds": 300,
    }
    stdout = stderr = ""
    try:
        before = checked_parent(run, parent, root)
        if before["binding_sha256"] != binding:
            raise ValueError("PCA 授权绑定已变化")
        metadata["source_before"] = before["source"]
        parent_directory = Path(parent["directory"]).resolve()
        script = directory / "script.R"
        code = pca_script(parent_directory, directory.resolve())
        script.write_text(code, encoding="utf-8")
        completed = executor.execute_script(str(script))
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        metadata["returncode"] = completed.returncode
        if completed.returncode != 0:
            raise ValueError("PCA R 执行失败")
        summary = _check_outputs(directory.resolve(), parent_directory)
        if script.read_text(encoding="utf-8") != code:
            raise ValueError("PCA 固定脚本发生变化")
        after = checked_parent(run, parent, root)
        metadata["source_after"] = after["source"]
        if after != before:
            raise ValueError("PCA 运行中父证据变化")
        metadata.update(summary, status="completed")
    except (RExecutorError, OSError, ValueError, KeyError, TypeError) as exc:
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
            Path(r["path"]).name: r
            for r in _fingerprints([p for p in directory.iterdir() if p.is_file()])
        }
        (directory / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return metadata
