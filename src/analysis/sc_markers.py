"""固定全细胞 Presto marker 协议；只产生探索性证据，不注释细胞类型。"""

import csv
import json
import math
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

from src.analysis.qc_preview import _digest
from src.analysis.r_executor import RExecutorError
from src.analysis.sc_cluster import _r_value, checked_completed_cluster
from src.analysis.sc_marker_reviews import (
    CLUSTER_RECORD_VERSION,
    review_binding,
)
from src.analysis.sc_pca import DIMENSION_RECORD_VERSION, _records
from src.analysis.sc_qc import _fingerprints

MARKER_TOOL_VERSION = "1.0.1"
PROTOCOL = {
    "schema": "sc-markers/1.0.0",
    "backend": "Seurat-FindAllMarkers-Presto-full",
    "features": "all_RNA_features",
    "normalization_source": "upstream_LogNormalize_no_override",
    "threads": {"future": "sequential", "Seurat": 1, "presto": 1},
    "markers": {
        "assay": "RNA",
        "slot": "data",
        "test.use": "wilcox",
        "only.pos": False,
        "min.pct": 0.01,
        "logfc.threshold": 0.1,
        "min.diff.pct": "-Inf",
        "max.cells.per.ident": "Inf",
        "random.seed": 42,
        "return.thresh": 0.01,
        "min.cells.feature": 3,
        "min.cells.group": 3,
        "latent.vars": None,
        "densify": False,
        "mean.fxn": None,
        "fc.name": "avg_log2FC",
        "base": 2,
        "norm.method": None,
        "pseudocount.use": 1,
        "fc.slot": "data",
        "verbose": True,
    },
    "p_adjust": "Bonferroni_all_RNA_genes",
    "return_filter": "raw_p_val < 0.01",
    "plot": {
        "positive_top_per_cluster": 5,
        "order": "p_val_adj asc, avg_log2FC desc, feature_key asc",
        "expression": "log1p(mean(expm1(RNA_data)))",
        "size": "fraction_RNA_data_gt_0",
        "width": "max(1200, 150 + 70 * clusters)",
        "height": "max(500, 140 + 25 * features)",
    },
    "timeout_seconds": 300,
    "packages": {
        "R": "4.6.1",
        "Seurat": "5.6.0",
        "SeuratObject": "5.4.0",
        "Matrix": "1.7.5",
        "presto": "1.1.0",
        "future": "1.80.0",
        "jsonlite": "2.0.0",
    },
}
MARKER_FIELDS = [
    "cluster_id",
    "feature_key",
    "feature_id",
    "symbol",
    "p_val",
    "avg_log2FC",
    "pct.1",
    "pct.2",
    "p_val_adj",
]
SUMMARY_FIELDS = [
    "cluster_id",
    "cells",
    "rest_cells",
    "test_status",
    "result_status",
    "positive_markers",
    "negative_markers",
]
PLOT_FIELDS = [
    "cluster_id",
    "feature_key",
    "feature_id",
    "symbol",
    "pct_expressing",
    "mean_expm1_data",
    "log1p_mean_expression",
]
OUTPUT_FILES = (
    "markers.csv",
    "cluster_marker_summary.csv",
    "marker_dotplot_data.csv",
    "marker_dotplot.png",
    "summary.json",
    "versions.txt",
    "session_info.txt",
    "script.R",
)


def checked_source(
    run: dict[str, Any], cluster: dict[str, Any], review: dict[str, Any], root: Path
) -> dict[str, Any]:
    """校验完整聚类及人工记录；不把执行推进的 revision 当作历史失效。"""
    summary = checked_completed_cluster(run, cluster, root, DIMENSION_RECORD_VERSION)
    latest = [
        r for r in run["cluster_reviews"] if r["cluster_attempt_id"] == cluster["attempt_id"]
    ][-1]
    if (
        latest != review
        or review["accepted"] is not True
        or review["record_version"] != CLUSTER_RECORD_VERSION
        or review["binding_sha256"] != review_binding(run, cluster)
    ):
        raise ValueError("聚类接受记录已变化")
    sizes = summary["cluster_sizes"]
    minimum = PROTOCOL["markers"]["min.cells.group"]
    if len(sizes) < 2 or any(
        s["cells"] < minimum or summary["cells"] - s["cells"] < minimum for s in sizes
    ):
        raise ValueError("marker 协议要求至少两簇，且每簇与其余各至少 3 细胞；不会合并小簇")
    pca = next(a for a in run["pca_attempts"] if a["attempt_id"] == cluster["pca_attempt_id"])
    return {
        "cluster_artifacts": cluster["result"]["artifacts"],
        "pca_artifacts": pca["result"]["artifacts"],
        "cells": summary["cells"],
        "clusters": summary["clusters"],
        "cluster_sizes": sizes,
    }


def execution_binding(run: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """自由文本和可变 revision 不进入分析身份。"""
    review = snapshot["review"]
    payload = {
        "session_id": run["session_id"],
        "run_id": run["run_id"],
        "source": snapshot["source"],
        "cluster_attempt_id": snapshot["cluster"]["attempt_id"],
        "review": {
            k: review[k]
            for k in (
                "review_id",
                "cluster_attempt_id",
                "binding_sha256",
                "record_version",
                "accepted",
            )
        },
        "tool_version": MARKER_TOOL_VERSION,
        "protocol": PROTOCOL,
    }
    return _digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())


def marker_script(cluster_directory: Path, pca_directory: Path, directory: Path) -> str:
    """只在独立 R 子进程 trace 固定版本快路径；NULL 或后备均立即失败。"""
    args = ",\n    ".join(
        f"{k}={v if v in ('Inf', '-Inf') else _r_value(v)}"
        if isinstance(v, str)
        else f"{k}={_r_value(v)}"
        for k, v in PROTOCOL["markers"].items()
        if k != "norm.method"
    )
    return f"""
suppressPackageStartupMessages(library(Seurat))
suppressPackageStartupMessages(library(jsonlite))
cluster_dir <- {json.dumps(cluster_directory.as_posix())}
pca_dir <- {json.dumps(pca_directory.as_posix())}
output_dir <- {json.dumps(directory.as_posix())}
required <- unlist(fromJSON({_r_value(json.dumps(PROTOCOL["packages"]))}))
versions <- c(R=as.character(getRversion()), sapply(names(required)[-1], function(p) as.character(packageVersion(p))))
stopifnot(identical(unname(versions), unname(required)))
writeLines(paste(names(versions), versions), file.path(output_dir, "versions.txt"))
future::plan({_r_value(PROTOCOL["threads"]["future"])})
Seurat::setThreads({PROTOCOL["threads"]["Seurat"]}L)
set.seed({PROTOCOL["markers"]["random.seed"]})
obj <- readRDS(file.path(cluster_dir, "seurat_clustered.rds"))
original <- readRDS(file.path(cluster_dir, "seurat_clustered.rds"))
mapping <- read.csv(file.path(pca_dir, "feature_mapping.csv"), colClasses="character", na.strings=character(), check.names=FALSE)
labels <- read.csv(file.path(cluster_dir, "clusters.csv"), colClasses="character", check.names=FALSE)
data <- GetAssayData(obj, assay="RNA", layer="data")
stopifnot(identical(colnames(obj), labels$barcode), identical(rownames(data), mapping$seurat_feature_key),
          identical(as.character(Idents(obj)), labels$cluster_id), all(is.finite(data@x)), all(data@x >= 0))
groups <- as.character(sort(as.integer(unique(labels$cluster_id))))
sizes <- vapply(groups, function(g) sum(labels$cluster_id == g), integer(1))
stopifnot(length(groups) >= 2L, all(sizes >= {PROTOCOL["markers"]["min.cells.group"]}L), all(ncol(obj) - sizes >= {PROTOCOL["markers"]["min.cells.group"]}L))
guard <- new.env(parent=emptyenv())
guard$presto_calls <- 0L; guard$fallback_calls <- 0L; guard$nthreads <- numeric(); guard$fastpath <- FALSE
trace("wilcoxauc.default", where=asNamespace("presto"), print=FALSE, tracer=quote({{
  guard$presto_calls <- guard$presto_calls + 1L
  guard$nthreads <- c(guard$nthreads, nthreads)
  if (!is.numeric(nthreads) || length(nthreads) != 1L || nthreads != {PROTOCOL["threads"]["presto"]}) stop("presto thread mismatch")
}}))
trace("WilcoxDETest", where=asNamespace("Seurat"), print=FALSE, tracer=quote({{
  guard$fallback_calls <- guard$fallback_calls + 1L
  stop("required presto backend; per-cluster fallback forbidden")
}}))
trace(".FindAllMarkersWilcoxPresto", where=asNamespace("Seurat"), print=FALSE, tracer=quote({{
  stopifnot(identical(norm.method, "LogNormalize"))
}}), exit=quote({{
  if (is.null(returnValue())) stop("required presto fastpath failed")
  guard$fastpath <- TRUE
}}))
warnings <- character()
markers <- withCallingHandlers(FindAllMarkers(obj, features=rownames(data),
    {args}
), warning=function(w) {{
  text <- conditionMessage(w); warnings <<- c(warnings, text)
  if (grepl("tests were not performed|When testing|error", text, ignore.case=TRUE)) stop(text)
}})
stopifnot(guard$fastpath, guard$fallback_calls == 0L, guard$presto_calls <= 1L)
untrace("wilcoxauc.default", where=asNamespace("presto"))
untrace("WilcoxDETest", where=asNamespace("Seurat"))
untrace(".FindAllMarkersWilcoxPresto", where=asNamespace("Seurat"))
if (!nrow(markers)) markers <- data.frame(cluster=character(), gene=character(), p_val=numeric(),
    avg_log2FC=numeric(), pct.1=numeric(), pct.2=numeric(), p_val_adj=numeric(), check.names=FALSE)
stopifnot(all(c("cluster", "gene", "p_val", "avg_log2FC", "pct.1", "pct.2", "p_val_adj") %in% names(markers)))
idx <- match(markers$gene, mapping$seurat_feature_key)
stopifnot(!anyNA(idx), all(as.character(markers$cluster) %in% groups))
out <- data.frame(cluster_id=as.character(markers$cluster), feature_key=markers$gene,
    feature_id=mapping$feature_id[idx], symbol=mapping$symbol[idx], markers[, c("p_val", "avg_log2FC", "pct.1", "pct.2", "p_val_adj")], check.names=FALSE)
out <- out[order(as.integer(out$cluster_id), out$p_val_adj, -out$avg_log2FC, out$feature_key), , drop=FALSE]
write.csv(out, file.path(output_dir, "markers.csv"), row.names=FALSE, na="")
positive <- vapply(groups, function(g) sum(out$cluster_id == g & out$avg_log2FC > 0), integer(1))
negative <- vapply(groups, function(g) sum(out$cluster_id == g & out$avg_log2FC < 0), integer(1))
summ <- data.frame(cluster_id=groups, cells=sizes, rest_cells=ncol(obj)-sizes, test_status="eligible",
    result_status=ifelse(positive+negative > 0L, "candidates_returned", "no_returned_candidates"),
    positive_markers=positive, negative_markers=negative)
write.csv(summ, file.path(output_dir, "cluster_marker_summary.csv"), row.names=FALSE)
selected <- unique(unlist(lapply(groups, function(g) head(out$feature_key[out$cluster_id == g & out$avg_log2FC > 0], {PROTOCOL["plot"]["positive_top_per_cluster"]}L))))
plot_data <- data.frame(cluster_id=character(), feature_key=character(), feature_id=character(), symbol=character(),
    pct_expressing=numeric(), mean_expm1_data=numeric(), log1p_mean_expression=numeric())
for (g in groups) for (key in selected) {{
  values <- as.numeric(data[key, labels$cluster_id == g]); m <- match(key, mapping$seurat_feature_key)
  avg <- mean(expm1(values))
  plot_data <- rbind(plot_data, data.frame(cluster_id=g, feature_key=key, feature_id=mapping$feature_id[m],
    symbol=mapping$symbol[m], pct_expressing=mean(values > 0), mean_expm1_data=avg, log1p_mean_expression=log1p(avg)))
}}
write.csv(plot_data, file.path(output_dir, "marker_dotplot_data.csv"), row.names=FALSE)
png(file.path(output_dir, "marker_dotplot.png"), width={PROTOCOL["plot"]["width"].replace("clusters", "length(groups)")}, height={PROTOCOL["plot"]["height"].replace("features", "length(selected)")})
if (!length(selected)) {{
  plot.new(); text(.5, .5, "No positive markers passed the fixed exploratory threshold")
}} else {{
  par(mar=c(5, 18, 4, 3)); palette <- colorRampPalette(c("#e4e6dc", "#137c72", "#003e37"))(100)
  value <- plot_data$log1p_mean_expression; high <- max(value)
  color <- if (high > 0) palette[pmax(1L, pmin(100L, 1L+floor(99*value/high)))] else palette[1]
  plot(match(plot_data$cluster_id, groups), match(plot_data$feature_key, selected),
       xlim=c(.5, length(groups)+.5), ylim=c(.5, length(selected)+.5), xaxt="n", yaxt="n",
       xlab="Exploratory cluster", ylab="", main="Positive marker candidates - unreviewed", pch=16,
       cex=2*sqrt(plot_data$pct_expressing), col=color)
  axis(1, seq_along(groups), groups); axis(2, seq_along(selected), selected, las=2, cex.axis=.8)
  mtext("Dot area: fraction expressing; color: log1p(mean normalized expression)", side=1, line=3)
}}
dev.off()
unchanged <- identical(obj, original)
stopifnot(unchanged)
writeLines(capture.output(sessionInfo()), file.path(output_dir, "session_info.txt"))
write_json(list(cells=ncol(obj), features=nrow(data), clusters=length(groups), marker_rows=nrow(out),
    positive_rows=sum(positive), negative_rows=sum(negative), plot_features=as.list(selected),
    backend={_r_value(PROTOCOL["backend"])}, fastpath_succeeded=guard$fastpath,
    presto_calls=guard$presto_calls, fallback_calls=guard$fallback_calls, presto_nthreads=as.list(guard$nthreads),
    source_object_unchanged=unchanged, seurat_threads=Seurat::getThreads(), warnings=as.list(warnings)),
    file.path(output_dir, "summary.json"), auto_unbox=TRUE, pretty=TRUE)
"""


def _table(path: Path, fields: list[str]) -> list[dict[str, str]]:
    """固定 schema 允许合法零行，不允许少字段或额外字段。"""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != fields:
            raise ValueError("marker 表字段不符")
        rows = list(reader)
    if any(set(r) != set(fields) or None in r.values() for r in rows):
        raise ValueError("marker 表结构不符")
    return rows


def check_outputs(directory: Path, pca_directory: Path, source: dict[str, Any]) -> dict[str, Any]:
    """独立核对原 feature 映射、簇状态、有限数值、阈值和绘图数据。"""
    if any(
        (directory / n).resolve().parent != directory
        or not (directory / n).is_file()
        or (directory / n).stat().st_size == 0
        for n in OUTPUT_FILES
    ):
        raise ValueError("marker 产物缺失、空或路径非法")
    mapping = {
        r["seurat_feature_key"]: r
        for r in _records((pca_directory / "feature_mapping.csv").read_bytes())
    }
    sizes = {r["cluster_id"]: r["cells"] for r in source["cluster_sizes"]}
    rows = _table(directory / "markers.csv", MARKER_FIELDS)
    summaries = _table(directory / "cluster_marker_summary.csv", SUMMARY_FIELDS)
    plot = _table(directory / "marker_dotplot_data.csv", PLOT_FIELDS)
    positive, negative = Counter(), Counter()
    seen = set()
    for r in rows:
        pair = (r["cluster_id"], r["feature_key"])
        m = mapping.get(r["feature_key"])
        values = {k: float(r[k]) for k in MARKER_FIELDS[4:]}
        if (
            pair in seen
            or pair[0] not in sizes
            or not m
            or r["feature_id"] != m["feature_id"]
            or r["symbol"] != m["symbol"]
            or not all(math.isfinite(v) for v in values.values())
            or any(not 0 <= values[k] <= 1 for k in ("p_val", "pct.1", "pct.2", "p_val_adj"))
            or values["p_val"] >= PROTOCOL["markers"]["return.thresh"]
            or abs(values["avg_log2FC"]) < PROTOCOL["markers"]["logfc.threshold"]
            or max(values["pct.1"], values["pct.2"]) < PROTOCOL["markers"]["min.pct"]
            or not math.isclose(
                values["p_val_adj"],
                min(values["p_val"] * len(mapping), 1),
                rel_tol=1e-9,
                abs_tol=1e-12,
            )
        ):
            raise ValueError("marker 候选数值、映射、重复或统计阈值非法")
        seen.add(pair)
        (positive if values["avg_log2FC"] > 0 else negative)[r["cluster_id"]] += 1
    groups = sorted(sizes, key=int)
    selected = []
    for g in groups:
        candidates = sorted(
            (r for r in rows if r["cluster_id"] == g and float(r["avg_log2FC"]) > 0),
            key=lambda r: (float(r["p_val_adj"]), -float(r["avg_log2FC"]), r["feature_key"]),
        )[: PROTOCOL["plot"]["positive_top_per_cluster"]]
        for r in candidates:
            if r["feature_key"] not in selected:
                selected.append(r["feature_key"])
    if [r["cluster_id"] for r in summaries] != groups:
        raise ValueError("marker 必须保存所有簇状态")
    for r in summaries:
        g = r["cluster_id"]
        if (
            int(r["cells"]) != sizes[g]
            or int(r["rest_cells"]) != source["cells"] - sizes[g]
            or r["test_status"] != "eligible"
            or int(r["positive_markers"]) != positive[g]
            or int(r["negative_markers"]) != negative[g]
            or r["result_status"]
            != ("candidates_returned" if positive[g] + negative[g] else "no_returned_candidates")
        ):
            raise ValueError("marker 簇摘要与候选表不符")
    if [(r["cluster_id"], r["feature_key"]) for r in plot] != [
        (g, k) for g in groups for k in selected
    ]:
        raise ValueError("marker 图必须覆盖所选正向基因的全部簇")
    for r in plot:
        m = mapping[r["feature_key"]]
        pct, avg, log = (float(r[k]) for k in PLOT_FIELDS[4:])
        if (
            r["feature_id"] != m["feature_id"]
            or r["symbol"] != m["symbol"]
            or not all(math.isfinite(v) for v in (pct, avg, log))
            or not 0 <= pct <= 1
            or avg < 0
            or not math.isclose(log, math.log1p(avg), rel_tol=1e-9, abs_tol=1e-12)
        ):
            raise ValueError("marker 绘图数值或映射非法")
    summary = json.loads((directory / "summary.json").read_bytes())
    expected = {
        "cells": source["cells"],
        "features": len(mapping),
        "clusters": len(sizes),
        "marker_rows": len(rows),
        "positive_rows": sum(positive.values()),
        "negative_rows": sum(negative.values()),
        "plot_features": selected,
        "backend": PROTOCOL["backend"],
        "fastpath_succeeded": True,
        "fallback_calls": 0,
        "source_object_unchanged": True,
        "seurat_threads": PROTOCOL["threads"]["Seurat"],
    }
    if (
        any(summary.get(k) != v for k, v in expected.items())
        or type(summary.get("presto_calls")) is not int
        or summary["presto_calls"] not in (0, 1)
        or (rows and summary["presto_calls"] != 1)
        or summary.get("presto_nthreads")
        != [PROTOCOL["threads"]["presto"]] * summary["presto_calls"]
        or not isinstance(summary.get("warnings"), list)
        or any(not isinstance(w, str) for w in summary["warnings"])
    ):
        raise ValueError("marker 后端执行证据或汇总不符")
    versions = (directory / "versions.txt").read_text().splitlines()
    if (
        versions != [f"{k} {v}" for k, v in PROTOCOL["packages"].items()]
        or (directory / "marker_dotplot.png").read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"
    ):
        raise ValueError("marker 版本或图非法")
    return {**summary, "versions": versions, "cluster_summary": summaries}


def run_sc_markers(
    run: dict[str, Any], attempt: dict[str, Any], root: Path, executor: Any
) -> dict[str, Any]:
    """事务外单次运行，保留父前后身份和本地日志；失败绝不后备或重试。"""
    snapshot = attempt["snapshot"]
    cluster, review = snapshot["cluster"], snapshot["review"]
    pca = next(a for a in run["pca_attempts"] if a["attempt_id"] == cluster["pca_attempt_id"])
    directory = Path(attempt["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    metadata = {
        "tool": "sc_markers",
        "tool_version": MARKER_TOOL_VERSION,
        "protocol": PROTOCOL,
        "run_id": run["run_id"],
        "binding_sha256": attempt["binding_sha256"],
        "cluster_review": {
            k: review[k]
            for k in (
                "review_id",
                "cluster_attempt_id",
                "binding_sha256",
                "record_version",
                "source_revision",
                "accepted",
            )
        },
        "source_revision": attempt["source_revision"],
        "claim_revision": attempt["claim_revision"],
        "stage": "marker_review",
        "scientific_status": "unreviewed",
        "status": "failed",
        "timeout_seconds": PROTOCOL["timeout_seconds"],
    }
    stdout = stderr = ""
    try:
        before = checked_source(run, cluster, review, root)
        if (
            before != snapshot["source"]
            or execution_binding(run, snapshot) != attempt["binding_sha256"]
        ):
            raise ValueError("marker 授权来源变化")
        metadata["source_before"] = before
        script = directory / "script.R"
        code = marker_script(
            Path(cluster["directory"]).resolve(), Path(pca["directory"]).resolve(), directory
        )
        script.write_text(code, encoding="utf-8")
        completed = executor.execute_script(str(script))
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        metadata["returncode"] = completed.returncode
        if completed.returncode != 0:
            raise ValueError("marker R 执行失败")
        summary = check_outputs(directory, Path(pca["directory"]).resolve(), before)
        if script.read_text(encoding="utf-8") != code:
            raise ValueError("marker 固定脚本变化")
        after = checked_source(run, cluster, review, root)
        metadata["source_after"] = after
        if before != after:
            raise ValueError("marker 运行中父证据变化")
        metadata.update(summary, status="completed")
    except (
        RExecutorError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
        StopIteration,
        IndexError,
    ) as exc:
        metadata["error"] = "marker 执行或证据校验失败；请检查本地证据，不会自动重跑"
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


def checked_completed_marker(
    run: dict[str, Any], attempt: dict[str, Any], root: Path
) -> dict[str, Any]:
    """恢复和图读取使用自己的授权快照，而非被认领推进的 current review。"""
    snapshot = attempt["snapshot"]
    cluster = next(
        a for a in run["cluster_attempts"] if a["attempt_id"] == attempt["cluster_attempt_id"]
    )
    if cluster != snapshot["cluster"]:
        raise ValueError("marker 聚类权威身份已变化")
    source = checked_source(run, cluster, snapshot["review"], root)
    directory = Path(attempt["directory"]).resolve()
    if directory != root.parent / "sc_markers" / attempt["attempt_id"]:
        raise ValueError("marker 目录非法")
    meta_path = directory / "metadata.json"
    if meta_path.resolve().parent != directory:
        raise ValueError("marker metadata 路径非法")
    meta = json.loads(meta_path.read_bytes())
    if (
        attempt["status"] != "completed"
        or attempt["tool_version"] != MARKER_TOOL_VERSION
        or json.loads(attempt["protocol"]) != PROTOCOL
        or execution_binding(run, snapshot) != attempt["binding_sha256"]
        or snapshot["source"] != source
        or meta != attempt["result"]
        or meta.get("status") != "completed"
        or meta.get("returncode") != 0
        or meta.get("source_before") != source
        or meta.get("source_after") != source
        or meta.get("protocol") != PROTOCOL
        or meta.get("binding_sha256") != attempt["binding_sha256"]
    ):
        raise ValueError("marker 权威结果或协议不符")
    names = set(OUTPUT_FILES) | {"stdout.txt", "stderr.txt"}
    if set(meta["artifacts"]) != names or any(
        (directory / n).resolve().parent != directory for n in names
    ):
        raise ValueError("marker 产物白名单不符")
    if {Path(r["path"]).name: r for r in _fingerprints([directory / n for n in names])} != meta[
        "artifacts"
    ]:
        raise ValueError("marker 产物身份变化")
    pca = next(a for a in run["pca_attempts"] if a["attempt_id"] == cluster["pca_attempt_id"])
    summary = check_outputs(directory, Path(pca["directory"]).resolve(), source)
    if any(meta.get(k) != v for k, v in summary.items()) or (directory / "script.R").read_text(
        encoding="utf-8"
    ) != marker_script(
        Path(cluster["directory"]).resolve(), Path(pca["directory"]).resolve(), directory
    ):
        raise ValueError("marker 固定脚本或汇总不符")
    return summary
