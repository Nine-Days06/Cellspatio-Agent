"""微型真实 Seurat/Matrix QC，无网络、无过滤或聚类。"""

import gzip
import shutil

import pytest

from src.analysis.r_executor import RExecutor
from src.analysis.sc_qc import run_sc_qc


@pytest.mark.parametrize("layout", ["csv", "legacy", "v3", "mixed", "unknown"])
def test_real_qc_all_barcodes_original_mt_symbols(tmp_path, layout):
    if not shutil.which("Rscript"):
        pytest.skip("Rscript 未安装")
    if layout in ("csv", "unknown"):
        path = tmp_path / "counts.csv"
        path.write_text(
            "gene,c1-1,c2-1,c3-1\n"
            + ("id1" if layout == "unknown" else "MT-X")
            + ",1,0,2\nG1,3,0,0\n"
        )
    else:
        path = tmp_path / "counts"
        path.mkdir()
        texts = {
            "matrix.mtx": "%%MatrixMarket matrix coordinate integer general\n3 3 4\n1 1 1\n2 1 3\n1 3 2\n3 1 99\n",
            "barcodes.tsv": "c1-1\nc2-1\nc3-1\n",
            "genes.tsv" if layout == "legacy" else "features.tsv": "id1\tMT-X\nid2\tMT-X\nid3\tG1\n"
            if layout == "legacy"
            else "id1\tMT-X\tGene Expression\nid2\tMT-X\tGene Expression\nid3\tAB\tAntibody Capture\n",
        }
        for index, (name, text) in enumerate(texts.items()):
            if layout == "v3" or (layout == "mixed" and index == 0):
                with gzip.open(path / (name + ".gz"), "wt") as handle:
                    handle.write(text)
            else:
                (path / name).write_text(text)
    result = run_sc_qc(str(path), RExecutor(), tmp_path / "outputs")
    assert result["status"] == "needs_confirmation", result
    qc = result["results"]["qc"]
    assert qc["cells"] == 3 and qc["zero_count_cells"] == 1
    assert qc["metrics"]["percent_mt"]["available"] == (0 if layout == "unknown" else 2)
    assert qc["mt_symbol_resolved"] == (layout != "unknown")
    if layout in ("v3", "mixed"):
        assert qc["features"] == 2 and qc["metrics"]["nCount_RNA"]["max"] == 4
        assert qc["metrics"]["percent_mt"]["median"] == 100
