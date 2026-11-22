"""KGQuery 只委托 KGMemory，不保留第二套检索路径。"""

from pathlib import Path

from src.control.kg_query import KGQuery


class _Memory:
    """记录委托调用的最小替身。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[str, str]] = []

    def query_text(self, query: str, mode: str = "mix") -> str:
        """返回固定回答并记录参数。"""
        self.calls.append((query, mode))
        return "回答"


def test_query_delegates_to_kg_memory() -> None:
    """问句与模式原样传给 KGMemory.query_text。"""
    memory = _Memory()
    query = KGQuery(Path("kg"), kg_memory=memory)  # type: ignore[arg-type]
    assert query.query("问题", "local") == "回答"
    assert memory.calls == [("问题", "local")]
