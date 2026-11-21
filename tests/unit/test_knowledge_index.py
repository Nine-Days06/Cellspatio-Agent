"""知识库覆盖索引：区分确认为空与无法确认。"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.control.kb_coverage import KnowledgeCoverage
from src.knowledge import knowledge_index
from src.knowledge.knowledge_index import KnowledgeIndex


def _file_index(tmp_path: Path) -> KnowledgeIndex:
    """构造文件后端索引。"""
    return KnowledgeIndex(workspace="kb", working_dir=tmp_path, backend="file")


def _write_files(tmp_path: Path, *, docs: dict[str, Any], entities: dict[str, Any]) -> None:
    """写入文件后端存储。"""
    (tmp_path / "kv_store_full_docs.json").write_text(
        json.dumps(docs, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "kv_store_full_entities.json").write_text(
        json.dumps(entities, ensure_ascii=False), encoding="utf-8"
    )


def test_pg_empty_library_is_confirmed_empty() -> None:
    """PG 查询成功但无行，返回空集而不是 None。"""
    index = KnowledgeIndex(workspace="kb", reader=lambda sql, params: [])

    assert index.snapshot() == (set(), set())


def test_pg_workspace_and_rows_are_parsed() -> None:
    """查询带 workspace，资产头与 JSONB 实体名统一小写。"""
    calls: list[tuple[str, tuple]] = []

    def reader(sql: str, params: tuple) -> list[dict[str, Any]]:
        calls.append((sql, params))
        if "LIGHTRAG_DOC_FULL" in sql:
            return [{"content": "# KEGG 通路: MAP04115\n# UniProt 蛋白: P04637"}]
        return [{"entity_names": ["TP53", "Map04115"]}]

    index = KnowledgeIndex(workspace="kb", reader=reader)

    assert index.snapshot() == ({"map04115", "p04637"}, {"tp53", "map04115"})
    assert all(params == ("kb",) for _, params in calls)


def test_pg_failure_is_unknown_not_empty() -> None:
    """连接/查询失败必须返回 None，避免把未知当空库。"""

    def reader(sql: str, params: tuple) -> list[dict[str, Any]]:
        raise OSError("pg unavailable")

    index = KnowledgeIndex(workspace="kb", reader=reader)

    assert index.snapshot() is None
    assert index.asset_ids() is None


def test_entity_only_query_skips_document_scan() -> None:
    """只有 genes 时不应拉取 full docs 正文。"""
    calls: list[str] = []

    def reader(sql: str, params: tuple) -> list[dict[str, Any]]:
        calls.append(sql)
        return [{"entity_names": '["TP53"]'}]

    index = KnowledgeIndex(workspace="kb", reader=reader)

    assert index.entity_names() == {"tp53"}
    assert all("LIGHTRAG_DOC_FULL" not in sql for sql in calls)


def test_malformed_entity_payload_is_unknown() -> None:
    """实体 JSON 损坏时返回 None，而不是空集。"""
    index = KnowledgeIndex(workspace="kb", reader=lambda sql, params: [{"entity_names": "{broken"}])

    assert index.entity_names() is None


def test_file_store_parses_and_missing_is_unknown(tmp_path: Path) -> None:
    """文件后端正常解析；文件缺失或损坏返回 None。"""
    _write_files(
        tmp_path,
        docs={"doc": {"content": "# UniProt 蛋白: P04637"}},
        entities={"doc": {"entity_names": ["TP53"]}},
    )
    index = _file_index(tmp_path)

    assert index.snapshot() == ({"p04637"}, {"tp53"})

    (tmp_path / "kv_store_full_entities.json").unlink()
    assert index.snapshot() is None

    (tmp_path / "kv_store_full_docs.json").write_text("{broken", encoding="utf-8")
    assert index.snapshot() is None

    (tmp_path / "kv_store_full_docs.json").write_bytes(b"\xff\xfe")
    (tmp_path / "kv_store_full_entities.json").write_text("{}", encoding="utf-8")
    assert index.snapshot() is None


def test_file_store_rejects_malformed_entity_payload(tmp_path: Path) -> None:
    """文件后端实体名不是 JSON 数组时视为未知。"""
    _write_files(tmp_path, docs={}, entities={"doc": {"entity_names": "TP53"}})
    index = _file_index(tmp_path)

    assert index.entity_names() is None


def test_default_pg_reader_errors_are_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """生产 PG 读取失败时返回 None，而不是把异常抛给问答。"""

    def broken(queries: list[str], workspace: str) -> list[list[dict[str, Any]]]:
        raise OSError("pg unavailable")

    monkeypatch.setattr(knowledge_index, "_pg_read", broken)
    index = KnowledgeIndex(workspace="kb")

    assert index.snapshot() is None


def test_from_client_requires_workspace() -> None:
    """无 workspace/working_dir 的 mock 不做覆盖判断。"""
    assert KnowledgeIndex.from_client(SimpleNamespace()) is None
    assert KnowledgeIndex.from_client(SimpleNamespace(working_dir=object())) is None


def test_from_client_prefers_existing_file_store(tmp_path: Path) -> None:
    """文件存在时使用文件后端，无需 PG。"""
    _write_files(tmp_path, docs={}, entities={})
    client = SimpleNamespace(working_dir=tmp_path)

    index = KnowledgeIndex.from_client(client)

    assert index is not None
    assert index.backend == "file"
    assert index.snapshot() == (set(), set())


def test_from_client_honors_explicit_backend(tmp_path: Path) -> None:
    """客户端声明的 PG 后端优先于残留文件。"""
    _write_files(tmp_path, docs={}, entities={})
    client = SimpleNamespace(working_dir=tmp_path, storage_backend="pg")

    index = KnowledgeIndex.from_client(client)

    assert index is not None
    assert index.backend == "pg"


def test_coverage_uses_injected_snapshot_once() -> None:
    """覆盖判断只读取一次索引，缺失 ID 触发补库、命中则不补。"""

    class FakeRegistry:
        def has(self, source: str) -> bool:
            return source == "kegg"

    class FakeIndex:
        def __init__(self, assets: set[str]) -> None:
            self.assets = assets
            self.asset_calls = 0

        def asset_ids(self) -> set[str]:
            self.asset_calls += 1
            return self.assets

        def entity_names(self) -> set[str]:
            raise AssertionError("无 genes 时不应查询实体名")

    coverage = KnowledgeCoverage(
        knowledge_client=None,
        builder=None,
        registry=FakeRegistry(),
        ingest_asset=lambda source, asset_id: {},
        index=FakeIndex({"map04115"}),
    )

    assert coverage.should_ingest("map04115 是什么", {}) is False
    assert coverage.index.asset_calls == 1
    assert coverage.should_ingest("map04110 是什么", {}) is True
    assert coverage.index.asset_calls == 2


def test_coverage_skips_index_without_explicit_ids_or_genes() -> None:
    """普通问题不触发知识库全量扫描。"""

    class FakeIndex:
        def asset_ids(self) -> set[str]:
            raise AssertionError("不应查询资产")

        def entity_names(self) -> set[str]:
            raise AssertionError("不应查询实体")

    class FakeClient:
        def query_context(self, query: str) -> str:
            return ""

    coverage = KnowledgeCoverage(
        knowledge_client=FakeClient(),
        builder=None,
        registry=None,
        ingest_asset=lambda source, asset_id: {},
        index=FakeIndex(),
    )

    assert coverage.should_ingest("什么是单细胞测序", {}) is True


def test_coverage_queries_entities_only_for_genes() -> None:
    """只有 genes 时只读实体侧，并据命中结果决定补库。"""

    class FakeIndex:
        def __init__(self) -> None:
            self.entity_calls = 0

        def asset_ids(self) -> set[str]:
            raise AssertionError("无显式 ID 时不应查询资产")

        def entity_names(self) -> set[str]:
            self.entity_calls += 1
            return {"tp53"}

    class FakeClient:
        def query_context(self, query: str) -> str:
            return ""

    coverage = KnowledgeCoverage(
        knowledge_client=FakeClient(),
        builder=None,
        registry=None,
        ingest_asset=lambda source, asset_id: {},
        index=FakeIndex(),
    )

    assert coverage.should_ingest("TP53 的功能", {"genes": ["TP53"]}) is False
    assert coverage.should_ingest("EGFR 的功能", {"genes": ["EGFR"]}) is True
    assert coverage.index.entity_calls == 2


def test_coverage_falls_back_to_context_when_index_unknown() -> None:
    """索引无法确认时回到 context 阈值，而不是当作命中或缺失。"""

    class UnknownIndex:
        def asset_ids(self) -> None:
            return None

        def entity_names(self) -> None:
            raise AssertionError("无 genes 时不应查询实体")

    class FakeClient:
        def query_context(self, query: str) -> str:
            return "short"

    coverage = KnowledgeCoverage(
        knowledge_client=FakeClient(),
        builder=None,
        registry=None,
        ingest_asset=lambda source, asset_id: {},
        index=UnknownIndex(),
    )

    assert coverage.should_ingest("map04115 是什么", {}) is True
