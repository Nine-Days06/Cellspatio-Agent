"""数据获取 lineage 单测"""
from src.data.lineage import append_lineage, read_lineage


def test_append_and_read_lineage(tmp_path):
    path = tmp_path / "lineage.jsonl"
    append_lineage(path, {
        "source": "geo", "asset_id": "GSE1", "title": "demo",
        "query": "肝癌 RNA-seq", "user": "local",
    })
    append_lineage(path, {
        "source": "geo", "asset_id": "GSE2", "title": "demo2",
        "query": "肝癌 RNA-seq", "user": "local",
    })
    rows = read_lineage(path)
    assert len(rows) == 2
    assert rows[0]["asset_id"] == "GSE1"
    assert "ts" in rows[0]


def test_read_missing_file_returns_empty(tmp_path):
    assert read_lineage(tmp_path / "nope.jsonl") == []
