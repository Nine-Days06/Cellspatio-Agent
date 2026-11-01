"""真实 R 第四片：内部 feature 键、counts 往返、独立公式和 SVD。"""

import csv
from pathlib import Path

import numpy as np
import pytest

from src.analysis.qc_preview import Thresholds, build_preview, register_evidence
from src.analysis.r_executor import RExecutor
from src.analysis.sc_filter import FILTER_TOOL_VERSION, run_sc_filter
from src.analysis.sc_pca import checked_parent, run_sc_pca
from src.analysis.sc_qc import run_sc_qc


def setup_parent(tmp_path, counts):
    """真实固定 QC/过滤链；只改变微型测试数据，不动公开基准。"""
    executor = RExecutor()
    source = tmp_path / "counts.csv"
    names = ["MT-X", "A_B", "A|B", "A-B", "ZERO", *[f"G{i}" for i in range(counts.shape[0] - 5)]]
    with source.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene", *[f"c{i}" for i in range(counts.shape[1])]])
        writer.writerows([name, *row] for name, row in zip(names, counts, strict=True))
    root = tmp_path / "data/analysis/sc_qc"
    qc = run_sc_qc(str(source), executor, root)
    assert qc["status"] == "needs_confirmation", qc
    directory = Path(qc["results"]["qc_artifacts"]["metadata.json"]).parent
    run = {
        "directory": str(directory),
        "run_id": directory.name,
        "evidence": register_evidence(directory, root),
    }
    preview = build_preview(
        run, root, Thresholds(n_feature_min=0, n_feature_max=1000, percent_mt_max=100)
    )
    preview["status"] = "approved"
    run.update(previews=[preview], active_preview_id=preview["preview_id"])
    out = root.parent / "sc_filter" / "parent"
    result = run_sc_filter(run, preview, root, out, executor)
    assert result["status"] == "completed", result
    parent = {
        "attempt_id": "parent",
        "preview_id": preview["preview_id"],
        "binding_sha256": preview["binding_sha256"],
        "directory": str(out),
        "status": "completed",
        "tool_version": FILTER_TOOL_VERSION,
        "result": result,
    }
    run["attempts"] = [parent]
    return executor, root, run, parent


def test_real_pca_formula_svd_order_and_unsafe_names(tmp_path):
    rng = np.random.default_rng(20261025)
    counts = rng.poisson(np.linspace(1, 30, 60)[:, None] * rng.uniform(0.3, 2, (60, 12)))
    counts[4] = 0
    executor, root, run, parent = setup_parent(tmp_path, counts)
    source_before = Path(run["evidence"]["input_path"]).read_bytes()
    parent_files = {p: p.read_bytes() for p in Path(parent["directory"]).iterdir() if p.is_file()}
    binding = checked_parent(run, parent, root)["binding_sha256"]
    output = root.parent / "sc_pca" / "test"
    result = run_sc_pca(run, parent, root, output, binding, executor)
    assert result["status"] == "completed", result
    assert result["actual_pcs"] == 11
    assert result["stage"] == "pca_review" and result["scientific_status"] == "unreviewed"
    assert source_before == Path(run["evidence"]["input_path"]).read_bytes()
    assert all(p.read_bytes() == before for p, before in parent_files.items())
    # 独立进程再读 RDS，并导出层；不调用生产脚本的 verify。
    executor.execute_code(f'''
        suppressPackageStartupMessages(library(Seurat))
        x <- readRDS("{(output / "seurat_pca.rds").as_posix()}")
        p <- readRDS("{(Path(parent["directory"]) / "filtered_counts.rds").as_posix()}")
        restored <- LayerData(x, assay="RNA", layer="counts")
        stopifnot(identical(rownames(restored), x@misc$feature_mapping$seurat_feature_key),
                  identical(colnames(restored), colnames(p)), identical(colnames(restored), colnames(x)))
        rownames(restored) <- x@misc$feature_mapping$parent_rowname
        stopifnot(identical(restored, p), length(x@graphs)==0L,
                  identical(names(x@reductions), "pca"), all(!grepl("_|[|]", rownames(x))))
        write.csv(as.matrix(LayerData(x, assay="RNA", layer="data")), "{(output / "test_normalized.csv").as_posix()}")
        write.csv(LayerData(x, assay="RNA", layer="scale.data"), "{(output / "test_scaled.csv").as_posix()}")
    ''')
    normalized = np.loadtxt(
        output / "test_normalized.csv", delimiter=",", skiprows=1, usecols=range(1, 13)
    )
    expected = np.log1p(counts * 10000 / counts.sum(axis=0))
    np.testing.assert_allclose(normalized, expected, atol=1e-12, rtol=1e-12)
    with (output / "test_scaled.csv").open() as handle:
        rows = list(csv.reader(handle))[1:]
    selected_indices = [int(row[0][1:]) - 1 for row in rows]
    scaled = np.array([[float(v) for v in row[1:]] for row in rows])
    expected_scaled = expected[selected_indices]
    centered = expected_scaled - expected_scaled.mean(axis=1, keepdims=True)
    sd = expected_scaled.std(axis=1, ddof=1, keepdims=True)
    expected_scaled = np.divide(centered, sd, out=np.zeros_like(centered), where=sd > 0)
    expected_scaled = np.minimum(expected_scaled, 10)
    np.testing.assert_allclose(scaled, expected_scaled, atol=1e-11, rtol=1e-11)
    singular = np.linalg.svd(scaled.T, compute_uv=False)[:11]
    stdev = np.loadtxt(output / "pca_stdev.csv", delimiter=",", skiprows=1, usecols=1)
    np.testing.assert_allclose(stdev, singular / np.sqrt(11), rtol=1e-6, atol=1e-8)
    embeddings = np.loadtxt(
        output / "pca_embeddings.csv", delimiter=",", skiprows=1, usecols=range(1, 12)
    )
    loadings = np.loadtxt(
        output / "pca_loadings.csv", delimiter=",", skiprows=1, usecols=range(1, 12)
    )
    with (output / "pca_loadings.csv").open() as handle:
        loading_keys = [row[0] for row in list(csv.reader(handle))[1:]]
    selected_keys = [row[0] for row in rows]
    usable_scaled = scaled[[selected_keys.index(key) for key in loading_keys]]
    np.testing.assert_allclose(embeddings, usable_scaled.T @ loadings, rtol=1e-6, atol=1e-8)
    np.testing.assert_allclose(loadings.T @ loadings, np.eye(11), atol=1e-7)


@pytest.mark.parametrize("cells", [2, 3])
def test_real_pca_insufficient_cells_or_degenerate_matrix_fails(tmp_path, cells):
    counts = np.repeat(np.arange(1, 61)[:, None], cells, axis=1)
    counts[4] = 0
    executor, root, run, parent = setup_parent(tmp_path, counts)
    binding = checked_parent(run, parent, root)["binding_sha256"]
    result = run_sc_pca(run, parent, root, root.parent / "sc_pca" / "degenerate", binding, executor)
    assert result["status"] == "failed", result
    assert result["scientific_status"] == "unreviewed"
    assert result["error"] and "seurat_pca.rds" not in result["artifacts"]
