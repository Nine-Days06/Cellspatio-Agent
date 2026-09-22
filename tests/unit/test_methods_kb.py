"""MethodsKb 单元测试（不触达真实 LightRAG）"""

from src.knowledge.methods_kb import MethodsKb


class FakeClient:
    def __init__(self):
        self.inserted: list[str] = []
        self.query_calls: list[dict] = []
        self._reply = "METHOD CARD CONTEXT"

    def insert_documents(self, documents: list[str]) -> int:
        self.inserted.extend(documents)
        return len(documents)

    def query_context(self, question: str, mode: str = "hybrid") -> str:
        self.query_calls.append({"question": question, "mode": mode})
        return self._reply


def test_build_from_card_files_reads_markdown(tmp_path):
    (tmp_path / "deseq2_de.md").write_text("# DESeq2\n## 常见坑\nx", encoding="utf-8")
    (tmp_path / "note.txt").write_text("ignore", encoding="utf-8")
    kb = MethodsKb(client=FakeClient())
    n = kb.build_from_cards(str(tmp_path))
    assert n == 1
    assert kb.client.inserted[0].startswith("# DESeq2")


def test_query_context_delegates_to_client():
    kb = MethodsKb(client=FakeClient())
    out = kb.query_context("差异表达 参数")
    assert out == "METHOD CARD CONTEXT"
    assert kb.client.query_calls[0]["question"] == "差异表达 参数"
    assert kb.client.query_calls[0]["mode"] == "hybrid"


def test_query_context_returns_empty_on_client_error():
    class Boom:
        def query_context(self, question: str, mode: str = "hybrid") -> str:
            raise RuntimeError("rag down")

    kb = MethodsKb(client=Boom())
    assert kb.query_context("x") == ""


def test_build_from_cards_missing_dir_returns_zero():
    kb = MethodsKb(client=FakeClient(), cards_dir="/nonexistent/path")
    assert kb.build_from_cards() == 0


def test_build_from_cards_empty_dir_returns_zero(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    kb = MethodsKb(client=FakeClient(), cards_dir=empty)
    assert kb.build_from_cards() == 0
