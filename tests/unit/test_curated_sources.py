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
