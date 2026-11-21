"""kb_ingest 的退出条件。"""

from kb_ingest import _idle


def test_idle_waits_for_processing_documents():
    """pending 清零但仍有 processing 时不能退出。"""
    assert _idle({"pending": 0, "processed": 10}) is True
    assert _idle({"pending": 0, "processing": 1}) is False
    assert _idle({"pending": 2, "processing": 1}) is False
