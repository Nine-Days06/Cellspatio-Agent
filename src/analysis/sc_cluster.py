"""固定邻居图、Louvain 与 UMAP；只消费 PCA，不生成或修复科学脚本。"""

from __future__ import annotations

import csv
import json
import math
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from src.analysis.qc_preview import _digest
from src.analysis.r_executor import RExecutorError
from src.analysis.sc_pca import PROTOCOL as PCA_PROTOCOL
from src.analysis.sc_pca import _records, checked_completed_pca, dimension_binding
from src.analysis.sc_qc import _fingerprints

CLUSTER_TOOL_VERSION = "1.0.0"
PROTOCOL = {
    "schema": "sc-cluster-umap/1.0.0",
    "minimum_cells": 31,
    "minimum_dims": 2,
    "seed": 42,
    "threads": {"future": "sequential", "Seurat": 1},
    "neighbors": {
        "reduction": "pca",
        "assay": "RNA",
        "k.param": 20,
        "return.neighbor": False,
        "compute.SNN": True,
        "prune.SNN": 1 / 15,
        "nn.method": "annoy",
        "n.trees": 50,
        "annoy.metric": "euclidean",
        "nn.eps": 0,
        "l2.norm": False,
        "cache.index": False,
        "graph.name": ["RNA_nn", "RNA_snn"],
        "verbose": True,
    },
    "clusters": {
        "graph.name": "RNA_snn",
        "modularity.fxn": 1,
        "resolution": 0.5,
        "algorithm": 1,
        "n.start": 10,
        "n.iter": 10,
        "random.seed": 42,
        "group.singletons": True,
        "initial.membership": None,
        "node.sizes": None,
        "cluster.name": "cluster_id",
        "verbose": True,
    },
    "umap": {
        "reduction": "pca",
        "assay": "RNA",
        "n.components": 2,
        "umap.method": "uwot",
        "uwot.init": "spectral",
        "n.neighbors": 30,
        "metric": "cosine",
        "min.dist": 0.3,
        "spread": 1,
        "learning.rate": 1,
        "set.op.mix.ratio": 1,
        "local.connectivity": 1,
        "repulsion.strength": 1,
        "negative.sample.rate": 5,
        "a": None,
        "b": None,
        "seed.use": 42,
        "uwot.sgd": False,
        "uwot.approx_pow": False,
        "return.model": False,
        "metric.kwds": None,
        "angular.rp.forest": False,
        "densmap": False,
        "dens.lambda": 2,
        "dens.frac": 0.3,
        "dens.var.shift": 0.1,
        "reduction.name": "umap",
        "reduction.key": "UMAP_",
        "verbose": True,
    },
    "epochs_rule": "cells <= 10000: 500; otherwise: 200",
    "timeout_seconds": 300,
    "packages": {
        **PCA_PROTOCOL["packages"],
        "RcppAnnoy": "0.0.23",
        "uwot": "0.2.5",
        "igraph": "2.3.4",
        "jsonlite": "2.0.0",
    },
}
OUTPUT_FILES = (
    "seurat_clustered.rds",
    "neighbors.rds",
    "clusters.csv",
    "umap_embeddings.csv",
    "cluster_sizes.csv",
    "umap_clusters.png",
    "summary.json",
    "versions.txt",
    "session_info.txt",
    "script.R",
)


def epochs_for(cells: int) -> int:
    """采用已核验 uwot 边界，不按耗时降参。"""
    return 500 if cells <= 10000 else 200


def checked_source(
    run: dict[str, Any],
    parent: dict[str, Any],
    pca: dict[str, Any],
    review: dict[str, Any],
    root: Path,
    record_version: str,
) -> dict[str, Any]:
    """验证记录的来源和适用性；不检查执行自身推进后的 current revision。"""
    checked = checked_completed_pca(run, parent, pca, root)
    dims = review["dims"]
    if (
        review["pca_attempt_id"] != pca["attempt_id"]
        or review["record_version"] != record_version
        or review["binding_sha256"] != dimension_binding(run, parent, pca, checked, record_version)
        or not isinstance(dims, list)
        or any(type(d) is not int for d in dims)
        or dims != list(range(1, len(dims) + 1))
        or not 2 <= len(dims) <= checked["actual_pcs"]
    ):
        raise ValueError("需要当前有效的至少 2 个连续前缀 PCs；请回上游明确记录，不自动补维")
    directory = Path(pca["directory"]).resolve()
    rows = _records((directory / "pca_embeddings.csv").read_bytes())
    if len(rows) < PROTOCOL["minimum_cells"]:
        raise ValueError("本项目固定 30-NN 协议要求至少 31 细胞；不缩邻居数或放宽 QC")
    values = np.array([[float(row[f"PC_{d}"]) for d in dims] for row in rows])
    with np.errstate(over="ignore", invalid="ignore"):
        norms = np.linalg.norm(values, axis=1)
        variance = np.var(values, axis=0)
    if not (
        np.isfinite(values).all()
        and np.isfinite(norms).all()
        and (norms > 0).all()
        and np.isfinite(variance).all()
        and (variance > 0).all()
    ):
        raise ValueError("所选 PCs 存在零范数、非有限或退化列；固定 cosine 协议不适用")
    return {
        "artifacts": checked["artifacts"],
        "cells": len(rows),
        "actual_pcs": checked["actual_pcs"],
        "dims": dims,
        "n_epochs": epochs_for(len(rows)),
    }


def execution_binding(run: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """稳定绑定包含确切 review 和完整源身份，但不含 revision/授权依据。"""
    review, parent, pca = snapshot["review"], snapshot["parent"], snapshot["pca"]
    payload = {
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "preview_id": parent["preview_id"],
        "filter_attempt_id": parent["attempt_id"],
        "pca_attempt_id": pca["attempt_id"],
        "pca_tool_version": pca["tool_version"],
        "pca_protocol": json.loads(pca["protocol"]),
        "source": snapshot["source"],
        "review": {k: review[k] for k in ("review_id", "binding_sha256", "record_version", "dims")},
        "tool_version": CLUSTER_TOOL_VERSION,
        "protocol": PROTOCOL,
    }
    return _digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())


def _r_value(value: Any) -> str:
    """只序列化服务端固定协议到 R 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, list):
        return "c(" + ",".join(_r_value(v) for v in value) + ")"
    return json.dumps(value)


def cluster_script(
    pca_directory: Path, parent_directory: Path, directory: Path, source: dict[str, Any]
) -> str:
    """参数从同一固定协议展开；两次读取对象与稀疏图均验证。"""
    args = {
        stage: ",\n    ".join(f"{k}={_r_value(v)}" for k, v in PROTOCOL[stage].items())
        for stage in ("neighbors", "clusters", "umap")
    }
    return f"""
suppressPackageStartupMessages(library(Seurat))
suppressPackageStartupMessages(library(Matrix))
suppressPackageStartupMessages(library(jsonlite))
pca_dir <- {json.dumps(pca_directory.as_posix())}
parent_dir <- {json.dumps(parent_directory.as_posix())}
output_dir <- {json.dumps(directory.as_posix())}
dims <- {_r_value(source["dims"])}
n_epochs <- {source["n_epochs"]}L
required <- unlist(fromJSON({_r_value(json.dumps(PROTOCOL["packages"]))}))
versions <- c(R=as.character(getRversion()), sapply(names(required)[-1], function(p) as.character(packageVersion(p))))
stopifnot(identical(unname(versions), unname(required)))
writeLines(paste(names(versions), versions), file.path(output_dir, "versions.txt"))
future::plan("sequential")
Seurat::setThreads(1L)
stopifnot(Seurat::getThreads(verbose=FALSE) == 1L)
writeLines(c(capture.output(sessionInfo()), paste("future", packageVersion("future")),
             paste("Seurat_threads", Seurat::getThreads(verbose=FALSE)), capture.output(extSoftVersion())),
           file.path(output_dir, "session_info.txt"))
set.seed(42)
read_table <- function(name) read.csv(file.path(pca_dir, name), check.names=FALSE,
                                     colClasses="character", na.strings=character())
original <- readRDS(file.path(pca_dir, "seurat_pca.rds"))
counts <- LayerData(original, assay="RNA", layer="counts")
filtered <- readRDS(file.path(parent_dir, "filtered_counts.rds"))
mapping <- read_table("feature_mapping.csv")
restored <- counts
stopifnot(inherits(counts, "dgCMatrix"), all(is.finite(counts@x)), all(counts@x >= 0),
          all(counts@x == floor(counts@x)), identical(rownames(counts), mapping$seurat_feature_key),
          identical(original@misc$feature_mapping, mapping))
rownames(restored) <- mapping$parent_rowname
stopifnot(identical(restored, filtered), length(original@graphs)==0L,
          identical(names(original@reductions), "pca"))
normalized <- LayerData(original, assay="RNA", layer="data")
expected <- counts
expected@x <- log1p(expected@x * rep(10000 / Matrix::colSums(counts), diff(counts@p)))
stopifnot(identical(dimnames(normalized), dimnames(counts)), max(abs(normalized - expected)) < 1e-10)
barcodes <- colnames(original)
embedding <- Embeddings(original, "pca")
csv_embedding <- read_table("pca_embeddings.csv")
loadings <- Loadings(original, "pca")
csv_loadings <- read_table("pca_loadings.csv")
csv_stdev <- read_table("pca_stdev.csv")
numeric_table <- function(x) {{ y <- as.matrix(x[,-1,drop=FALSE]); storage.mode(y) <- "double"; y }}
stopifnot(identical(rownames(embedding), barcodes), identical(csv_embedding$barcode, barcodes),
          identical(rownames(loadings), csv_loadings$seurat_feature_key),
          max(abs(embedding - numeric_table(csv_embedding))) < 1e-10,
          max(abs(loadings - numeric_table(csv_loadings))) < 1e-10,
          max(abs(Stdev(original, "pca") - as.numeric(csv_stdev$stdev))) < 1e-10)
selected <- embedding[,dims,drop=FALSE]
stopifnot(nrow(selected) >= 31L, length(dims) >= 2L, all(is.finite(selected)),
          all(is.finite(rowSums(selected^2))), all(rowSums(selected^2) > 0),
          all(apply(selected, 2L, var) > 0), n_epochs == if(ncol(original) <= 10000L) 500L else 200L)
obj <- FindNeighbors(original, dims=dims,
    {args["neighbors"]})
obj <- FindClusters(obj,
    {args["clusters"]})
obj <- RunUMAP(obj, dims=dims, n.epochs=n_epochs,
    {args["umap"]})
graphs <- obj@graphs[c("RNA_nn", "RNA_snn")]
labels <- as.character(obj$cluster_id)
umap <- Embeddings(obj, "umap")
verify <- function(x) {{
    for(layer in c("counts", "data", "scale.data")) stopifnot(identical(
        LayerData(x, assay="RNA", layer=layer), LayerData(original, assay="RNA", layer=layer)))
    stopifnot(identical(x@misc$feature_mapping, original@misc$feature_mapping),
              identical(VariableFeatures(x), VariableFeatures(original)), identical(x[["pca"]], original[["pca"]]),
              identical(colnames(x), barcodes), identical(names(x@graphs), c("RNA_nn", "RNA_snn")),
              identical(names(x@reductions), c("pca", "umap")), identical(x@graphs, graphs))
    for(g in x@graphs) stopifnot(inherits(g, "sparseMatrix"), identical(dim(g), c(length(barcodes), length(barcodes))),
        identical(rownames(g), barcodes), identical(colnames(g), barcodes), all(is.finite(g@x)), all(g@x >= 0))
    stopifnot(isSymmetric(x[["RNA_snn"]]), !anyNA(x$cluster_id), identical(as.character(x$cluster_id), labels),
              identical(as.character(Idents(x)), labels), length(labels)==length(barcodes),
              all(grepl("^[0-9]+$", labels)), identical(Embeddings(x, "umap"), umap),
              identical(rownames(umap), barcodes), ncol(umap)==2L, all(is.finite(umap)))
}}
verify(obj)
saveRDS(obj, file.path(output_dir, "seurat_clustered.rds"))
verify(readRDS(file.path(output_dir, "seurat_clustered.rds")))
saveRDS(graphs, file.path(output_dir, "neighbors.rds"))
stopifnot(identical(readRDS(file.path(output_dir, "neighbors.rds")), graphs))
write.csv(data.frame(barcode=barcodes, cluster_id=labels), file.path(output_dir, "clusters.csv"), row.names=FALSE)
write.csv(data.frame(barcode=barcodes, umap, check.names=FALSE), file.path(output_dir, "umap_embeddings.csv"), row.names=FALSE)
sizes <- table(labels)
sizes <- data.frame(cluster_id=names(sizes), cells=as.integer(sizes), proportion=as.integer(sizes)/length(labels))
stopifnot(sum(sizes$cells)==length(barcodes))
write.csv(sizes, file.path(output_dir, "cluster_sizes.csv"), row.names=FALSE)
png(file.path(output_dir, "umap_clusters.png"), width=1200, height=800)
palette <- hcl.colors(nrow(sizes), "Dark 3")
plot(umap, pch=16, cex=0.4, col=palette[match(labels, sizes$cluster_id)], xlab="UMAP 1", ylab="UMAP 2",
     main="Exploratory clusters / scientifically unreviewed")
legend("topright", legend=paste("Cluster", sizes$cluster_id), col=palette, pch=16, cex=0.8)
dev.off()
write_json(list(cells=length(barcodes), dims=I(dims), n_epochs=n_epochs, clusters=nrow(sizes),
                nn_nonzero=length(graphs$RNA_nn@x), snn_nonzero=length(graphs$RNA_snn@x),
                counts_unchanged=TRUE, pca_unchanged=TRUE, seurat_threads=Seurat::getThreads(verbose=FALSE),
                future_version=as.character(packageVersion("future"))),
           file.path(output_dir, "summary.json"), auto_unbox=TRUE)
"""


def check_outputs(directory: Path, pca_directory: Path, source: dict[str, Any]) -> dict[str, Any]:
    """Python 独立交叉核对逐细胞顺序、计数、数值与显式协议。"""
    if any(
        (directory / n).resolve().parent != directory
        or not (directory / n).is_file()
        or (directory / n).stat().st_size == 0
        for n in OUTPUT_FILES
    ):
        raise ValueError("聚类产物缺失、空或路径非法")
    barcodes = [r["barcode"] for r in _records((pca_directory / "pca_embeddings.csv").read_bytes())]
    tables = {}
    for name, fields in (
        ("clusters.csv", ["barcode", "cluster_id"]),
        ("umap_embeddings.csv", ["barcode", "UMAP_1", "UMAP_2"]),
        ("cluster_sizes.csv", ["cluster_id", "cells", "proportion"]),
    ):
        with (directory / name).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != fields:
                raise ValueError("聚类数值表字段不符")
            tables[name] = list(reader)
    labels = tables["clusters.csv"]
    umap = tables["umap_embeddings.csv"]
    if (
        [r["barcode"] for r in labels] != barcodes
        or [r["barcode"] for r in umap] != barcodes
        or any(not r["cluster_id"].isascii() or not r["cluster_id"].isdigit() for r in labels)
        or any(not math.isfinite(float(r[k])) for r in umap for k in ("UMAP_1", "UMAP_2"))
    ):
        raise ValueError("聚类/UMAP 数值或 barcode 顺序非法")
    counts = Counter(r["cluster_id"] for r in labels)
    sizes = tables["cluster_sizes.csv"]
    if (
        len(sizes) != len(counts)
        or {r["cluster_id"]: int(r["cells"]) for r in sizes} != counts
        or any(
            not math.isclose(
                float(r["proportion"]), counts[r["cluster_id"]] / len(barcodes), abs_tol=1e-12
            )
            for r in sizes
        )
    ):
        raise ValueError("簇大小与逐细胞标签不一致")
    summary = json.loads((directory / "summary.json").read_bytes())
    expected = {
        "cells": len(barcodes),
        "dims": source["dims"],
        "n_epochs": source["n_epochs"],
        "clusters": len(counts),
        "counts_unchanged": True,
        "pca_unchanged": True,
        "seurat_threads": 1,
    }
    if (
        any(summary.get(k) != v for k, v in expected.items())
        or any(
            type(summary.get(k)) is not int or not 0 < summary[k] <= len(barcodes) ** 2
            for k in ("nn_nonzero", "snn_nonzero")
        )
        or not isinstance(summary.get("future_version"), str)
    ):
        raise ValueError("聚类汇总与固定协议不一致")
    versions = (directory / "versions.txt").read_text().splitlines()
    if versions != [f"{k} {v}" for k, v in PROTOCOL["packages"].items()]:
        raise ValueError("聚类包版本不符")
    if (directory / "umap_clusters.png").read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("UMAP 图非法")
    return {
        **summary,
        "versions": versions,
        "cluster_sizes": [
            {
                "cluster_id": r["cluster_id"],
                "cells": int(r["cells"]),
                "proportion": float(r["proportion"]),
            }
            for r in sizes
        ],
    }


def run_sc_cluster(
    run: dict[str, Any], attempt: dict[str, Any], root: Path, executor: Any
) -> dict[str, Any]:
    """事务外只执行一次；快照独立于当前 revision，失败不修复或降参。"""
    snapshot = attempt["snapshot"]
    parent, pca, review = snapshot["parent"], snapshot["pca"], snapshot["review"]
    directory = Path(attempt["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    metadata = {
        "tool": "sc_cluster",
        "tool_version": CLUSTER_TOOL_VERSION,
        "protocol": PROTOCOL,
        "run_id": run["run_id"],
        "binding_sha256": attempt["binding_sha256"],
        "dimension_review": {
            k: review[k]
            for k in (
                "review_id",
                "pca_attempt_id",
                "binding_sha256",
                "record_version",
                "source_revision",
                "dims",
            )
        },
        "source_revision": attempt["source_revision"],
        "claim_revision": attempt["claim_revision"],
        "stage": "cluster_review",
        "scientific_status": "unreviewed",
        "status": "failed",
        "timeout_seconds": 300,
    }
    stdout = stderr = ""
    try:
        before = checked_source(run, parent, pca, review, root, review["record_version"])
        if (
            before != snapshot["source"]
            or execution_binding(run, snapshot) != attempt["binding_sha256"]
        ):
            raise ValueError("聚类授权来源已变化")
        metadata["source_before"] = before
        script = directory / "script.R"
        code = cluster_script(
            Path(pca["directory"]).resolve(), Path(parent["directory"]).resolve(), directory, before
        )
        script.write_text(code, encoding="utf-8")
        completed = executor.execute_script(str(script))
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        metadata["returncode"] = completed.returncode
        if completed.returncode != 0:
            raise ValueError("聚类 R 执行失败；请检查本地证据")
        summary = check_outputs(directory, Path(pca["directory"]).resolve(), before)
        if script.read_text(encoding="utf-8") != code:
            raise ValueError("聚类固定脚本已变化")
        after = checked_source(run, parent, pca, review, root, review["record_version"])
        metadata["source_after"] = after
        if before != after:
            raise ValueError("聚类运行中父证据变化")
        metadata.update(summary, status="completed")
    except (RExecutorError, OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        metadata["error"] = "聚类执行或证据校验失败；请检查本地证据，不会自动重跑"
        # 异常可能含私有路径；只保存到本地 stderr，不进入响应、消息或日志。
        stderr = f"{stderr}\n{exc}"
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


def checked_completed_cluster(
    run: dict[str, Any], attempt: dict[str, Any], root: Path, record_version: str
) -> dict[str, Any]:
    """按自己的快照验证历史；新 review/父变化不能冒充当前结果。"""
    snapshot = attempt["snapshot"]
    pca = next(a for a in run["pca_attempts"] if a["attempt_id"] == attempt["pca_attempt_id"])
    parent = next(a for a in run["attempts"] if a["attempt_id"] == pca["filter_attempt_id"])
    latest = [d for d in run["dimension_reviews"] if d["pca_attempt_id"] == pca["attempt_id"]][-1]
    if latest != snapshot["review"] or run["evidence"] != snapshot["evidence"]:
        raise ValueError("父身份或维度记录已更新；本次结果仅为历史")
    source = checked_source(run, parent, pca, latest, root, record_version)
    if (
        source != snapshot["source"]
        or execution_binding(run, snapshot) != attempt["binding_sha256"]
    ):
        raise ValueError("聚类源证据或协议已变化；本次结果仅为历史")
    directory = Path(attempt["directory"]).resolve()
    if directory != root.parent / "sc_clusters" / attempt["attempt_id"]:
        raise ValueError("聚类目录非法")
    meta_path = directory / "metadata.json"
    if meta_path.resolve().parent != directory:
        raise ValueError("聚类 metadata 路径非法")
    meta = json.loads(meta_path.read_bytes())
    if (
        attempt["status"] != "completed"
        or attempt["tool_version"] != CLUSTER_TOOL_VERSION
        or json.loads(attempt["protocol"]) != PROTOCOL
        or meta != attempt["result"]
        or meta.get("status") != "completed"
        or meta.get("returncode") != 0
        or meta.get("source_before") != source
        or meta.get("source_after") != source
        or meta.get("protocol") != PROTOCOL
        or meta.get("binding_sha256") != attempt["binding_sha256"]
    ):
        raise ValueError("聚类权威结果与协议不符")
    names = set(OUTPUT_FILES) | {"stdout.txt", "stderr.txt"}
    if set(meta["artifacts"]) != names or any(
        (directory / n).resolve().parent != directory for n in names
    ):
        raise ValueError("聚类登记白名单不符")
    identities = {Path(r["path"]).name: r for r in _fingerprints([directory / n for n in names])}
    if identities != meta["artifacts"]:
        raise ValueError("聚类文件身份已变化")
    summary = check_outputs(directory, Path(pca["directory"]).resolve(), source)
    if any(meta.get(k) != v for k, v in summary.items()) or (directory / "script.R").read_text(
        encoding="utf-8"
    ) != cluster_script(
        Path(pca["directory"]).resolve(), Path(parent["directory"]).resolve(), directory, source
    ):
        raise ValueError("聚类固定脚本或汇总不一致")
    return summary
