"""用安装的 Seurat 检查微型三件套读取，不运行聚类或联网。"""

import gzip
import json
import shutil
import subprocess
from itertools import product

import pytest

from src.control.r_script_generator import RScriptGenerator
from src.control.workflow_manager import WorkflowManager


@pytest.mark.parametrize("field", ["integer", "real"])
def test_real_seurat_loads_tenx_layouts(tmp_path, field):
    """覆盖 16 种布局、多模态筛选、稀疏性及模板 R 语法。"""
    rscript = shutil.which("Rscript")
    if not rscript:
        pytest.skip("Rscript 未安装")
    checks = [
        'if (!requireNamespace("Seurat", quietly = TRUE)) quit(status = 77)',
        'cat("Seurat version:", as.character(packageVersion("Seurat")), "\\n")',
    ]
    for index, (legacy, compressed) in enumerate(
        product((False, True), product((False, True), repeat=3))
    ):
        directory = tmp_path / f"layout.{index}"
        directory.mkdir()
        texts = (
            (
                "matrix.mtx",
                f"%%MatrixMarket matrix coordinate {field} general\n2 2 2\n"
                + ("1 1 1.0\n2 2 2e0\n" if field == "real" else "1 1 1\n2 2 2\n"),
            ),
            ("barcodes.tsv", "c1-1\nc2-1\n"),
            (
                "genes.tsv" if legacy else "features.tsv",
                "id1\tMT-X\nid2\tMT-X\n"
                if legacy
                else "id1\tMT-X\tGene Expression\nid2\tMT-X\tGene Expression\n",
            ),
        )
        for (name, text), zipped in zip(texts, compressed, strict=True):
            path = directory / (name + (".gz" if zipped else ""))
            if zipped:
                with gzip.open(path, "wt", encoding="utf-8") as handle:
                    handle.write(text)
            else:
                path.write_text(text, encoding="utf-8")
        assert WorkflowManager._validate_sc_input(str(directory)) is None
        checks.append(RScriptGenerator._single_cell_input_code({"input_file": str(directory)}))
        checks.append(
            'stopifnot(inherits(mat, "sparseMatrix"), identical(dim(mat), c(2L, 2L)), '
            'identical(rownames(mat), c("MT-X", "MT-X.1")), '
            'identical(colnames(mat), c("c1-1", "c2-1")), mat[1, 1] == 1, mat[2, 2] == 2)'
        )
        if not legacy:
            rna_directory = tmp_path / f"rna.{index}"
            shutil.copytree(directory, rna_directory)
            feature_path = rna_directory / ("features.tsv.gz" if compressed[2] else "features.tsv")
            text = "id1\tMT-X\tGene Expression\nid2\tAB1\tAntibody Capture\n"
            if compressed[2]:
                with gzip.open(feature_path, "wt", encoding="utf-8") as handle:
                    handle.write(text)
            else:
                feature_path.write_text(text, encoding="utf-8")
            checks.append(
                RScriptGenerator._single_cell_input_code({"input_file": str(rna_directory)})
            )
            checks.append(
                'stopifnot(inherits(mat, "sparseMatrix"), identical(dim(mat), c(1L, 2L)), '
                'rownames(mat)[1] == "MT-X", mat[1, 1] == 1, mat[1, 2] == 0)'
            )
    template = RScriptGenerator().generate_code(
        "single_cell",
        {
            "input_file": r"D:\sample.v1",
            "output_file": r"D:\out.csv",
            "marker_file": r"D:\markers.csv",
        },
    )
    checks.append(f"invisible(parse(text = {json.dumps(template, ensure_ascii=False)}))")
    checks.append('cat("10x Seurat load checks passed\\n")')
    script = tmp_path / "load_checks.R"
    script.write_text("\n".join(checks), encoding="utf-8")
    result = subprocess.run(
        [rscript, "--vanilla", str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode == 77:
        pytest.skip("Seurat 未安装")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "10x Seurat load checks passed" in result.stdout
