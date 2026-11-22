"""知识库覆盖索引：区分「确认为空」与「无法确认」。"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ASSET_HEADER_RE = re.compile(
    r"^#\s*(?:KEGG\s+通路|UniProt\s+蛋白):\s*(\S+)",
    re.IGNORECASE | re.MULTILINE,
)

DOC_SQL = (
    "SELECT content FROM LIGHTRAG_DOC_FULL WHERE workspace=$1 "
    "AND (content LIKE '%# KEGG 通路:%' OR content LIKE '%# UniProt 蛋白:%')"
)
ENTITY_SQL = "SELECT entity_names FROM LIGHTRAG_FULL_ENTITIES WHERE workspace=$1"
PG_TIMEOUT = 5

Reader = Callable[[str, Sequence[Any]], list[Mapping[str, Any]]]


def _lower_names(raw: Any) -> set[str] | None:
    """把 JSON/数据库返回的实体名统一成小写集合；格式未知时返回 None。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, list):
        return None
    return {str(name).lower() for name in raw}


class KnowledgeIndex:
    """读取知识库文档资产与实体名；失败时返回 None 而不是空集。"""

    def __init__(
        self,
        *,
        workspace: str,
        working_dir: str | Path | None = None,
        backend: str = "pg",
        reader: Reader | None = None,
    ) -> None:
        """保存后端、workspace 与可注入的查询函数。"""
        if backend not in ("pg", "file"):
            raise ValueError("knowledge index backend 只能是 pg 或 file")
        self.workspace = workspace
        self.working_dir = Path(working_dir) if working_dir is not None else None
        self.backend = backend
        self._reader = reader

    @classmethod
    def from_client(cls, client: Any) -> KnowledgeIndex | None:
        """从 LightRAG client 推导 workspace；无可用目录时返回 None。"""
        working_dir = getattr(client, "working_dir", None)
        workspace = getattr(client, "workspace", None)
        try:
            if not workspace and working_dir:
                from src.knowledge.lightrag_client import default_workspace_for

                workspace = default_workspace_for(working_dir)
            base = Path(working_dir) if working_dir else None
        except (TypeError, ValueError, OSError):
            return None
        if not workspace:
            return None
        explicit_backend = getattr(client, "storage_backend", None)
        if explicit_backend in ("pg", "file"):
            return cls(workspace=workspace, working_dir=base, backend=explicit_backend)
        backend = "file" if base and _has_file_store(base) else "pg"
        return cls(workspace=workspace, working_dir=base, backend=backend)

    def snapshot(self) -> tuple[set[str], set[str]] | None:
        """一次读取资产 ID 与实体名；任一失败即整体返回 None。"""
        return self._read(assets=True, entities=True)

    def asset_ids(self) -> set[str] | None:
        """文档头里的 KEGG/UniProt 资产 ID（小写）；无法确认时 None。"""
        result = self._read(assets=True, entities=False)
        return result[0] if result is not None else None

    def entity_names(self) -> set[str] | None:
        """实体表中的实体名（小写）；无法确认时 None。"""
        result = self._read(assets=False, entities=True)
        return result[1] if result is not None else None

    def _read(self, *, assets: bool, entities: bool) -> tuple[set[str], set[str]] | None:
        """按需读取所需的一侧或两侧；任何读取失败返回 None。"""
        if self.backend == "file":
            return self._file_snapshot(assets=assets, entities=entities)
        return self._pg_snapshot(assets=assets, entities=entities)

    def _pg_snapshot(self, *, assets: bool, entities: bool) -> tuple[set[str], set[str]] | None:
        """查询 PG 的 full docs / full entities 表。"""
        try:
            queries = []
            if assets:
                queries.append(DOC_SQL)
            if entities:
                queries.append(ENTITY_SQL)
            if self._reader is not None:
                result_rows = [self._reader(sql, (self.workspace,)) for sql in queries]
            else:
                result_rows = _pg_read(queries, self.workspace)
            asset_ids: set[str] = set()
            names: set[str] = set()
            cursor = iter(result_rows)
            if assets:
                asset_ids = _asset_ids(str(row.get("content") or "") for row in next(cursor))
            if entities:
                for row in next(cursor):
                    parsed = _lower_names(row.get("entity_names"))
                    if parsed is None:
                        logger.warning("knowledge index entity payload is malformed")
                        return None
                    names |= parsed
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
            StopIteration,
        ) as exc:
            logger.warning("knowledge index pg read failed: %s", exc)
            return None
        return asset_ids, names

    def _file_snapshot(self, *, assets: bool, entities: bool) -> tuple[set[str], set[str]] | None:
        """读取文件后端的 full docs / full entities。"""
        if self.working_dir is None:
            return None
        docs_path = self.working_dir / "kv_store_full_docs.json"
        entities_path = self.working_dir / "kv_store_full_entities.json"
        try:
            docs = json.loads(docs_path.read_text(encoding="utf-8")) if assets else {}
            entities = json.loads(entities_path.read_text(encoding="utf-8")) if entities else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("knowledge index file read failed: %s", exc)
            return None
        if not isinstance(docs, dict) or not isinstance(entities, dict):
            logger.warning("knowledge index file payload is not an object")
            return None
        contents = (
            (
                str(item.get("content", "")) if isinstance(item, Mapping) else str(item)
                for item in docs.values()
            )
            if assets
            else ()
        )
        names: set[str] = set()
        if entities:
            for item in entities.values():
                if not isinstance(item, Mapping):
                    logger.warning("knowledge index entity payload is malformed")
                    return None
                parsed = _lower_names(item.get("entity_names"))
                if parsed is None:
                    logger.warning("knowledge index entity payload is malformed")
                    return None
                names |= parsed
        return _asset_ids(contents), names


def _has_file_store(base: Path) -> bool:
    """目录中存在任一文件后端存储时判定为 file 后端。"""
    return (base / "kv_store_full_docs.json").exists() or (
        base / "kv_store_full_entities.json"
    ).exists()


def _asset_ids(contents: Iterable[str]) -> set[str]:
    """从文档内容中提取专属文档头资产 ID。"""
    return {match.lower() for content in contents for match in ASSET_HEADER_RE.findall(content)}


def _pg_read(queries: Sequence[str], workspace: str) -> list[list[Mapping[str, Any]]]:
    """在同一连接上执行一组只读 PG 查询。"""
    import asyncpg

    from src.knowledge.async_runtime import runtime
    from src.knowledge.lightrag_client import PG_ENV_DEFAULTS

    async def fetch() -> list[list[Mapping[str, Any]]]:
        config = {
            "host": os.getenv("POSTGRES_HOST", PG_ENV_DEFAULTS["POSTGRES_HOST"]),
            "port": int(os.getenv("POSTGRES_PORT", PG_ENV_DEFAULTS["POSTGRES_PORT"])),
            "user": os.getenv("POSTGRES_USER", PG_ENV_DEFAULTS["POSTGRES_USER"]),
            "password": os.getenv("POSTGRES_PASSWORD", PG_ENV_DEFAULTS["POSTGRES_PASSWORD"]),
            "database": os.getenv("POSTGRES_DATABASE", PG_ENV_DEFAULTS["POSTGRES_DATABASE"]),
        }
        conn = await asyncpg.connect(**config, timeout=PG_TIMEOUT)
        try:
            return [await conn.fetch(sql, workspace, timeout=PG_TIMEOUT) for sql in queries]
        finally:
            await conn.close()

    try:
        return runtime.run(fetch())
    except (OSError, RuntimeError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise RuntimeError(f"knowledge index pg read failed: {exc}") from exc
