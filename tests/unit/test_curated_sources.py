"""curated_sources 单测：下载缓存 / 条目装箱 / CellMarker / Cell Ontology 文本化。"""

import httpx
import pytest

from src.knowledge.curated_sources import ensure_file


def test_ensure_file_skips_when_exists(tmp_path):
    """已缓存文件直接返回，不发起下载请求。"""
    target = tmp_path / "cached.bin"
    target.write_bytes(b"cached")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=b"new")

    path = ensure_file(
        "http://example.test/f.bin",
        target,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert path == target
    assert path.read_bytes() == b"cached"
    assert calls["n"] == 0  # 已存在不发请求


def test_ensure_file_downloads_with_retry(tmp_path):
    """首次失败后重试成功，且不留 .part 临时文件。"""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500)  # 第一次失败，第二次成功
        return httpx.Response(200, content=b"payload")

    path = ensure_file(
        "http://example.test/f.bin",
        tmp_path / "sub" / "f.bin",
        retries=3,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert path.read_bytes() == b"payload"
    assert calls["n"] == 2
    assert not list(tmp_path.glob("*.part"))  # 无残留临时文件


def test_ensure_file_exhausts_retries(tmp_path):
    """重试耗尽抛 RuntimeError，且不留下半截文件。"""

    def handler(request):
        return httpx.Response(500)

    with pytest.raises(RuntimeError):
        ensure_file(
            "http://example.test/f.bin",
            tmp_path / "f.bin",
            retries=2,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    assert not (tmp_path / "f.bin").exists()  # 失败不留下半截文件


from src.knowledge.curated_sources import _pack_entries


def test_pack_entries_greedy_packing():
    """贪心装箱按预算累积条目，超预算时封箱。"""
    entries = ["A" * 10, "B" * 10, "C" * 10]
    # header 4 字符，budget = 30 - 4 - 1 = 25；A+B=21 ≤ 25，再加 C=32 > 25 → 分两箱
    docs = _pack_entries(entries, "# H:", max_chars=30)
    assert docs == [
        "# H:\n" + "A" * 10 + "\n" + "B" * 10,
        "# H:\n" + "C" * 10,
    ]


def test_pack_entries_oversized_entry_own_box():
    """超预算单条独占一箱，不与后续条目混合。"""
    # 单条 30 > budget 15 → 独占一箱，不与后续混合
    docs = _pack_entries(["x" * 30, "y" * 5], "# H:", max_chars=20)
    assert docs == ["# H:\n" + "x" * 30, "# H:\n" + "y" * 5]


def test_pack_entries_empty():
    """空条目列表返回空文档列表。"""
    assert _pack_entries([], "# H:") == []


def _write_cellmarker_xlsx(path) -> None:
    """最小 CellMarker fixture：2 个细胞类型、数值 PMID、CL 下划线写法。"""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["cell_name", "cellontology_id", "tissue_type", "marker", "PMID"])
    ws.append(["B cell", "CL_0000235", "blood", "CD19", 12345678])
    ws.append(["B cell", "CL:0000235", "blood", "MS4A1", 12345678])
    ws.append(["T cell", "CL_0000084", "thymus", "CD3E", 87654321])
    ws.append(["", "CL_0000000", "empty", "GENE", 11111111])  # cell_name 空 → 跳过
    wb.save(path)


def test_cellmarker_texts_aggregates_entries(tmp_path):
    """按细胞名聚合多行条目，归一 CL 写法并跳过空名行。"""
    from src.data.gene_map import GeneIndex
    from src.knowledge.curated_sources import cellmarker_texts

    xlsx = tmp_path / "Cell_marker_Human.xlsx"
    _write_cellmarker_xlsx(xlsx)
    docs = cellmarker_texts(xlsx, gene_index=GeneIndex({}, {}, {}))
    assert len(docs) == 1
    doc = docs[0]
    assert doc.startswith("# CellMarker 细胞类型:")
    assert "## B cell" in doc
    assert "Cell Ontology ID: CL:0000235" in doc  # CL_ 写法归一为 CL:
    assert "组织：blood" in doc
    assert "标志物：CD19, MS4A1" in doc  # 聚合两行、保持首现顺序
    assert "文献：PMID 12345678（共 1 篇）" in doc  # PMID 去重
    assert "## T cell" in doc
    assert "CD3E" in doc
    assert "GENE" not in doc  # cell_name 为空的行被跳过


def test_cellmarker_texts_uses_ensure_file_when_no_path(monkeypatch, tmp_path):
    """不传路径时经 ensure_file 按 CELLMARKER_URL 取缓存。"""
    import src.knowledge.curated_sources as cs
    from src.data.gene_map import GeneIndex

    xlsx = tmp_path / "cached.xlsx"
    _write_cellmarker_xlsx(xlsx)
    called = {}

    def fake_ensure(url, path, **kwargs):
        called["url"] = url
        return xlsx

    monkeypatch.setattr(cs, "ensure_file", fake_ensure)
    docs = cs.cellmarker_texts(
        gene_index=GeneIndex({}, {}, {})
    )  # 不传路径 → 走 ensure_file(CELLMARKER_URL)
    assert called["url"] == cs.CELLMARKER_URL
    assert docs


def test_cellmarker_markers_inline_gene_ids(tmp_path):
    """命中的 marker 行内标注规范 token。"""
    from src.data.gene_map import GeneIndex
    from src.knowledge.curated_sources import cellmarker_texts

    xlsx = tmp_path / "Cell_marker_Human.xlsx"
    _write_cellmarker_xlsx(xlsx)
    docs = cellmarker_texts(xlsx, gene_index=GeneIndex({"CD19": "930", "MS4A1": "931"}, {}, {}))
    assert "标志物：CD19 (NCBIGene:930), MS4A1 (NCBIGene:931)" in docs[0]


def test_cellmarker_markers_unmapped_stay_bare(tmp_path):
    """未命中（小鼠符号/退役符号）保持裸符号，不塞占位。"""
    from src.data.gene_map import GeneIndex
    from src.knowledge.curated_sources import cellmarker_texts

    xlsx = tmp_path / "Cell_marker_Human.xlsx"
    _write_cellmarker_xlsx(xlsx)
    docs = cellmarker_texts(xlsx, gene_index=GeneIndex({}, {}, {}))
    assert "标志物：CD19, MS4A1" in docs[0]


def test_cellmarker_uses_singleton_index_when_not_injected(monkeypatch, tmp_path):
    """不传索引时回落到进程单例（此处 monkeypatch，避免联网下载）。"""
    import src.knowledge.curated_sources as cs
    from src.data.gene_map import GeneIndex

    xlsx = tmp_path / "Cell_marker_Human.xlsx"
    _write_cellmarker_xlsx(xlsx)
    monkeypatch.setattr(cs, "get_gene_index", lambda: GeneIndex({"CD19": "930"}, {}, {}))
    docs = cs.cellmarker_texts(xlsx)
    assert "CD19 (NCBIGene:930)" in docs[0]


OBO_FIXTURE = """format-version: 1.2
data-version: releases/2026-09-01

[Term]
id: CL:0000084
name: T cell
def: "A type of lymphocyte." [GOC:bt]
is_a: CL:0000051 ! lymphocyte
relationship: part_of CL:0000089 ! immune system
comment: Includes CD4 and CD8 subsets.

[Term]
id: CL:0000002
name: obsolete thing
is_obsolete: true

[Term]
id: GO:0008150
name: biological process

[Typedef]
id: develops_from
name: develops from
"""


def test_cell_ontology_texts_filters_and_formats(tmp_path):
    """过滤废弃/非 CL 术语并按管道格式拼接条目。"""
    from src.knowledge.curated_sources import cell_ontology_texts

    obo = tmp_path / "cl.obo"
    obo.write_text(OBO_FIXTURE, encoding="utf-8")
    docs = cell_ontology_texts(obo)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.startswith("# Cell Ontology 术语:")
    assert (
        "CL:0000084 T cell | is_a: CL:0000051 lymphocyte | "
        "relationship: part_of CL:0000089 ! immune system | "
        "comment: Includes CD4 and CD8 subsets." in doc
    )
    assert "GO:0008150" not in doc  # 非 CL id 过滤
    assert "CL:0000002" not in doc  # 废弃术语过滤
    assert "develops_from" not in doc  # Typedef（id 非 CL:）过滤


def test_cell_ontology_texts_uses_ensure_file_when_no_path(monkeypatch, tmp_path):
    """不传路径时经 ensure_file 按 CL_OBO_URL 取缓存。"""
    import src.knowledge.curated_sources as cs

    obo = tmp_path / "cached.obo"
    obo.write_text(OBO_FIXTURE, encoding="utf-8")
    called = {}

    def fake_ensure(url, path, **kwargs):
        called["url"] = url
        return obo

    monkeypatch.setattr(cs, "ensure_file", fake_ensure)
    docs = cs.cell_ontology_texts()  # 不传路径 → 走 ensure_file(CL_OBO_URL)
    assert called["url"] == cs.CL_OBO_URL
    assert docs
