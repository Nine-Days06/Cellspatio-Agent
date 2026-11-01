"""微型真实 R 稀疏过滤、全部行/一列与 exact RDS 往返。"""

import gzip
import shutil
from pathlib import Path

import pytest

from src.analysis.qc_preview import Thresholds, build_preview, register_evidence
from src.analysis.r_executor import RExecutor
from src.analysis.sc_filter import run_sc_filter
from src.analysis.sc_qc import run_sc_qc


@pytest.mark.parametrize("layout", ["csv", "legacy", "v3", "mixed"])
@pytest.mark.parametrize("keep_all", [False, True])
def test_real_filter_sparse_exact_rows_names_and_single_column(tmp_path, layout, keep_all):
    if not shutil.which("Rscript"):
        pytest.skip("Rscript 未安装")
    executor = RExecutor()
    if layout == "csv":
        source = tmp_path / "counts.csv"
        source.write_text("gene,c1,c2\nMT-X,1,1\nG1,0,3\nZERO,0,0\n")
    else:
        source = tmp_path / "counts"
        source.mkdir()
        texts = {
            "matrix.mtx": "%%MatrixMarket matrix coordinate integer general\n4 2 4\n1 1 1\n1 2 1\n2 2 3\n4 1 99\n",
            "barcodes.tsv": "c1\nc2\n",
            "genes.tsv"
            if layout == "legacy"
            else "features.tsv": "id1\tMT-X\nid2\tMT-X\nid3\tZERO\nid4\tG2\n"
            if layout == "legacy"
            else "id1\tMT-X\tGene Expression\nid2\tMT-X\tGene Expression\nid3\tZERO\tGene Expression\nid4\tAB\tAntibody Capture\n",
        }
        for index, (name, text) in enumerate(texts.items()):
            if layout == "v3" or (layout == "mixed" and index == 0):
                with gzip.open(source / (name + ".gz"), "wt") as handle:
                    handle.write(text)
            else:
                (source / name).write_text(text)
    root = tmp_path / "qc"
    qc = run_sc_qc(str(source), executor, root)
    assert qc["status"] == "needs_confirmation", qc
    directory = Path(qc["results"]["qc_artifacts"]["metadata.json"]).parent
    run = {
        "directory": str(directory),
        "run_id": directory.name,
        "evidence": register_evidence(directory, root),
    }
    # 多模态 RNA 两列都是 MT counts=100；用 counts 下限筛一列。
    params = Thresholds(
        n_feature_min=0,
        n_feature_max=100,
        percent_mt_max=100,
        n_count_min=None if keep_all else (50 if layout == "legacy" else 2),
    )
    preview = build_preview(run, root, params)
    out = tmp_path / "filtered"
    result = run_sc_filter(run, preview, root, out, executor)
    assert result["status"] == "completed", result
    assert result["cells"] == (2 if keep_all else 1)
    assert result["features"] == (4 if layout == "legacy" else 3)
    # 由独立真实 R 再打开产物，零行和列名仍在；生产脚本也已 exact identical。
    executor.execute_code(
        f'r <- readRDS("{(out / "filtered_counts.rds").as_posix()}"); stopifnot(inherits(r, "sparseMatrix"), any(Matrix::rowSums(r) == 0), !is.null(colnames(r)), !is.null(rownames(r)))'
    )
