"""curated_sources 单测：下载缓存 / 条目装箱 / CellMarker / Cell Ontology 文本化。"""

import httpx
import pytest

from src.knowledge.curated_sources import ensure_file


def test_ensure_file_skips_when_exists(tmp_path):
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
    entries = ["A" * 10, "B" * 10, "C" * 10]
    # header 4 字符，budget = 30 - 4 - 1 = 25；A+B=21 ≤ 25，再加 C=32 > 25 → 分两箱
    docs = _pack_entries(entries, "# H:", max_chars=30)
    assert docs == [
        "# H:\n" + "A" * 10 + "\n" + "B" * 10,
        "# H:\n" + "C" * 10,
    ]


def test_pack_entries_oversized_entry_own_box():
    # 单条 30 > budget 15 → 独占一箱，不与后续混合
    docs = _pack_entries(["x" * 30, "y" * 5], "# H:", max_chars=20)
    assert docs == ["# H:\n" + "x" * 30, "# H:\n" + "y" * 5]


def test_pack_entries_empty():
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
    from src.knowledge.curated_sources import cellmarker_texts

    xlsx = tmp_path / "Cell_marker_Human.xlsx"
    _write_cellmarker_xlsx(xlsx)
    docs = cellmarker_texts(xlsx)
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
    import src.knowledge.curated_sources as cs

    xlsx = tmp_path / "cached.xlsx"
    _write_cellmarker_xlsx(xlsx)
    called = {}

    def fake_ensure(url, path, **kwargs):
        called["url"] = url
        return xlsx

    monkeypatch.setattr(cs, "ensure_file", fake_ensure)
    docs = cs.cellmarker_texts()  # 不传路径 → 走 ensure_file(CELLMARKER_URL)
    assert called["url"] == cs.CELLMARKER_URL
    assert docs
