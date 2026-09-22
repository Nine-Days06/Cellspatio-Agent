"""建库脚本单测"""

from src.knowledge.build_methods_kb import build_methods_kb


def test_build_methods_kb_returns_inserted_count(tmp_path):
    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "a.md").write_text("# A", encoding="utf-8")
    (cards / "b.md").write_text("# B", encoding="utf-8")

    class FakeClient:
        def insert_documents(self, documents):
            return len(documents)

    from src.knowledge.methods_kb import MethodsKb

    kb = MethodsKb(client=FakeClient())
    assert build_methods_kb(kb, cards_dir=cards) == 2
