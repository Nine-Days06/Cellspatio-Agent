"""10x 输入契约、工作流与确认后复检回归。"""

import gzip
from itertools import product
from unittest.mock import Mock

import pytest

from src.control.r_script_generator import RScriptGenerator
from src.control.workflow_manager import WorkflowManager
from tests.unit.fakes import FakeExec, FakeGen, FakeIntent


@pytest.fixture
def tenx_dir(tmp_path):
    """写微型三件套，每个文件可独立压缩。"""

    def write(legacy=False, compressed=(False, False, False), overrides=None):
        directory = tmp_path / "sample.v1"
        directory.mkdir()
        texts = {
            "matrix.mtx": "%%MatrixMarket matrix coordinate integer general\n% counts\n2 2 2\n1 1 1\n2 2 2\n",
            "barcodes.tsv": "c1-1\nc2-1\n",
            "genes.tsv" if legacy else "features.tsv": (
                "id1\tMT-X\nid2\tMT-X\n"
                if legacy
                else "id1\tMT-X\tGene Expression\nid2\tMT-X\tGene Expression\n"
            ),
        }
        texts.update(overrides or {})
        for (name, text), zipped in zip(texts.items(), compressed, strict=True):
            path = directory / (name + (".gz" if zipped else ""))
            if zipped:
                with gzip.open(path, "wt", encoding="utf-8") as handle:
                    handle.write(text)
            else:
                path.write_text(text, encoding="utf-8")
        return directory

    return write


def _manager(require=True):
    """隔离网络与执行，用真实工作流驱动输入门禁。"""
    return WorkflowManager(
        intent_parser=FakeIntent(),
        knowledge_client=None,
        r_executor=FakeExec(track_codes=True),
        visualizer=None,
        r_script_generator=FakeGen(return_value="# script", track_calls=True),
        require_script_confirmation=require,
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("compressed", list(product((False, True), repeat=3)))
def test_layouts_route_and_preserve_directory_names(tenx_dir, legacy, compressed, monkeypatch):
    directory = tenx_dir(legacy=legacy, compressed=compressed)
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    qc = Mock(return_value={"status": "needs_confirmation", "stage": "qc_review"})
    monkeypatch.setattr("src.analysis.sc_qc.run_sc_qc", qc)
    wm = _manager()
    result = wm.run_analysis_for_agent("single_cell", {"input_files": [str(directory)]})
    assert result["status"] == "needs_confirmation"
    assert qc.call_args.args[0] == str(directory)
    assert wm.r_script_generator.calls == []
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == original


@pytest.mark.parametrize("require", [False, True])
@pytest.mark.parametrize(
    "matrix",
    [
        "",
        "%%MatrixMarket matrix array integer general\n2 2\n1\n",
        "%%MatrixMarket matrix coordinate complex general\n2 2 1\n1 1 1\n",
        "%%MatrixMarket matrix coordinate integer symmetric\n2 2 1\n1 1 1\n",
        "%%MatrixMarket matrix coordinate integer general\n2 3 1\n1 1 1\n",
        "%%MatrixMarket matrix coordinate integer general\n3 2 1\n1 1 1\n",
        "%%MatrixMarket matrix coordinate integer general\n2 2\n",
        "%%MatrixMarket matrix coordinate integer general\n2 x 1\n",
        "%%MatrixMarket matrix coordinate integer general\n2 2 2\n1 1 1\n",
        "%%MatrixMarket matrix coordinate integer general\n2 2 0\n1 1 1\n",
    ],
)
def test_invalid_matrix_stops_before_codegen(tenx_dir, matrix, require):
    directory = tenx_dir(overrides={"matrix.mtx": matrix})
    wm = _manager(require)
    wm.methods_kb = Mock()
    result = wm.run_analysis_for_agent("single_cell", {"input_files": [str(directory)]})
    assert result["status"] == "error"
    assert wm.r_script_generator.calls == []
    assert wm.r_executor.codes == []
    wm.methods_kb.query_context.assert_not_called()


@pytest.mark.parametrize(
    "entry",
    [
        "0 1 1",
        "3 1 1",
        "1 0 1",
        "1 3 1",
        "1.0 1 1",
        "1 1 -1",
        "1 1 0.5",
        "1 1 1.00000000000000000001",
        "1 1 1e2",
        "1 1 NaN",
        "1 1 Inf",
        "1 1 " + "9" * 400,
        "1 1",
        "1 1 1 2",
    ],
)
def test_invalid_coordinate_or_count(tenx_dir, entry):
    directory = tenx_dir(
        overrides={
            "matrix.mtx": f"%%MatrixMarket matrix coordinate integer general\n2 2 1\n{entry}\n",
        }
    )
    assert WorkflowManager._validate_sc_input(str(directory))["status"] == "error"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("zipped", [False, True])
@pytest.mark.parametrize("count", ["1", "1.0", "1e2", "+2.00", "0e-30"])
def test_real_header_integer_counts_route_without_rewriting(tenx_dir, legacy, zipped, count):
    directory = tenx_dir(
        legacy=legacy,
        compressed=(zipped,) * 3,
        overrides={
            "matrix.mtx": f"%%MatrixMarket matrix coordinate real general\n2 2 1\n1 1 {count}\n"
        },
    )
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert WorkflowManager._validate_sc_input(str(directory)) is None
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == original


@pytest.mark.parametrize(
    "entry",
    [
        "1.0 1 1",
        "1 0 1",
        "1 1 -1",
        "1 1 0.5",
        "1 1 1.00000000000000000001",
        "1 1 1e-400",
        "1 1 1e309",
        "1 1 NaN",
        "1 1 Inf",
        "1 1 1_000",
        "1 1",
        "1 1 1 2",
    ],
)
def test_real_header_rejects_invalid_counts_before_codegen(tenx_dir, entry):
    directory = tenx_dir(
        overrides={
            "matrix.mtx": f"%%MatrixMarket matrix coordinate real general\n2 2 1\n{entry}\n",
        }
    )
    wm = _manager()
    assert (
        wm.run_analysis_for_agent("single_cell", {"input_files": [str(directory)]})["status"]
        == "error"
    )
    assert wm.r_script_generator.calls == []
    assert wm.r_executor.codes == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"barcodes.tsv": ""},
        {"barcodes.tsv": "c1\nc1\n"},
        {"barcodes.tsv": "c1\n\n"},
        {"barcodes.tsv": "c1\tother\nc2\tother\n"},
        {"features.tsv": "id1\tG1\tGene Expression\nid1\tG2\tGene Expression\n"},
        {"features.tsv": "id1\t\tGene Expression\nid2\tG2\tGene Expression\n"},
        {"features.tsv": "id1\tG1\nid2\tG2\n"},
        {"features.tsv": "id1\tG1\tAntibody Capture\nid2\tG2\tAntibody Capture\n"},
    ],
)
def test_invalid_identifiers_or_no_rna(tenx_dir, overrides):
    directory = tenx_dir(overrides=overrides)
    assert WorkflowManager._validate_sc_input(str(directory))["status"] == "error"


@pytest.mark.parametrize("name", ["matrix.mtx", "barcodes.tsv", "features.tsv"])
def test_missing_component(tenx_dir, name):
    directory = tenx_dir()
    (directory / name).unlink()
    assert WorkflowManager._validate_sc_input(str(directory))["status"] == "error"


@pytest.mark.parametrize(
    "extra", ["genes.tsv", "features.tsv.gz", "matrix.mtx.gz", "barcodes.tsv.gz"]
)
def test_ambiguous_component_is_rejected(tenx_dir, extra):
    directory = tenx_dir()
    (directory / extra).write_bytes(b"extra")
    assert WorkflowManager._validate_sc_input(str(directory))["status"] == "error"


def test_multimodal_and_zero_counts_are_accepted(tenx_dir):
    directory = tenx_dir(
        overrides={
            "features.tsv": "id1\tG1\tGene Expression\nid2\tAB1\tAntibody Capture\n",
            "matrix.mtx": "%%MatrixMarket matrix coordinate integer general\n2 2 1\n1 1 0\n",
        }
    )
    assert WorkflowManager._validate_sc_input(str(directory)) is None


def test_old_single_cell_confirmation_is_rejected(tenx_dir):
    directory = tenx_dir()
    wm = _manager()
    result = wm.execute_confirmed_script(
        "single_cell", {"input_file": str(directory)}, "stop('must not run')"
    )
    assert result["status"] == "error"
    assert wm.r_script_generator.calls == []
    assert wm.r_executor.codes == []


@pytest.mark.parametrize("extension", ["h5", "h5ad", "rds", "fastq", "mtx"])
def test_unsupported_file_rejected_even_if_content_is_csv(tmp_path, extension):
    path = tmp_path / f"counts.{extension}"
    path.write_text("gene,c1\nG1,1\n", encoding="utf-8")
    assert WorkflowManager._validate_sc_input(str(path))["status"] == "error"


def test_template_sparse_loading_and_windows_paths():
    params = {
        "input_file": r"D:\sample.v1",
        "output_file": r"D:\sc.csv",
        "marker_file": r"D:\marks.csv",
    }
    code = RScriptGenerator().generate_code("single_cell", params)
    assert 'input_file <- "D:\\\\sample.v1"' in code
    assert 'output_file <- "D:\\\\sc.csv"' in code
    assert "Read10X" in code and "ReadMtx" in code
    assert 'mat <- mat[["Gene Expression"]]' in code
    assert "mat <- mat[rna, , drop = FALSE]" in code
    assert "mat@x else mat" in code
    assert "as.matrix(mat)" not in code
    assert "round(mat)" not in code
    assert 'grepl("^genes\\\\.tsv"' in code
