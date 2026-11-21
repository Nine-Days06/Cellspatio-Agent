import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.knowledge.knowledge_index import KnowledgeIndex

# 按需补库：显式 ID 不在库中时触发；context 过短作为 mock/空库回退
# （LightRAG 在非空图上几乎总是返回 30k+ 松散匹配，长度阈值不可靠）
LAZY_INGEST_MIN_CONTEXT = int(os.environ.get("LAZY_INGEST_MIN_CONTEXT", "300"))
# 单次提问最多自动入库条数（控延迟与体积）
LAZY_INGEST_MAX_ASSETS = int(os.environ.get("LAZY_INGEST_MAX_ASSETS", "3"))
# 试点范围：暂不含 GEO（搜索噪声大）
LAZY_INGEST_SOURCES = ("kegg", "uniprot")

# KEGG: map04115 / hsa04115 / ko04115
_KEGG_ID_RE = re.compile(r"\b(?:map|hsa|ko)\d{5}\b", re.IGNORECASE)
# UniProt accession: P04637 / Q9Y2B4 / A0A0B4J2F0（6 或 10/15 位）
_UNIPROT_ACC_RE = re.compile(
    r"\b(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\b"
)

logger = logging.getLogger(__name__)


class KnowledgeCoverage:
    """知识覆盖判断与按需补库。"""

    def __init__(
        self,
        knowledge_client: Any,
        builder: Any,
        registry: Any,
        ingest_asset: Callable[[str, str], dict[str, Any]],
        logger: logging.Logger = logger,
        index: KnowledgeIndex | None = None,
    ) -> None:
        """保存知识库、构建器、注册表与入库回调。"""
        self.knowledge_client = knowledge_client
        self.builder = builder
        self.registry = registry
        self.ingest_asset = ingest_asset
        self.logger = logger
        self.index = index if index is not None else KnowledgeIndex.from_client(knowledge_client)

    def should_ingest(self, query: str, params: dict[str, Any]) -> bool:
        """是否触发按需补库。

        能读到知识库时：
        - 显式 KEGG/UniProt ID：按「专属文档头」判断（`# KEGG 通路: id` / `# UniProt 蛋白: id`）；
          实体表里的 map 号常是其它通路的交叉引用，不能当作已入库。
        - genes：按实体名判断（基因符号通常以实体出现）。
        - 显式标识均已命中 → 不补（避免重复入库）。
        读不到库路径（mock）或无显式标识：回退 context 长度阈值。
        """
        explicit = self.extract_ids(query)
        genes = [str(g) for g in (params.get("genes") or [])]

        unknown = False
        if explicit:
            asset_ids = self.index.asset_ids() if self.index is not None else None
            if asset_ids is None:
                unknown = True
            elif any(aid.lower() not in asset_ids for _src, aid in explicit):
                return True
        if genes:
            entities = self.index.entity_names() if self.index is not None else None
            if entities is None:
                unknown = True
            elif any(g.lower() not in entities for g in genes):
                return True
        # 显式 ID / genes 均已命中，不因 context 短而重复入库
        if (explicit or genes) and not unknown:
            return False

        ctx = self.context_for_miss_check(query)
        if ctx is None:
            return False
        return len(ctx.strip()) < LAZY_INGEST_MIN_CONTEXT

    def extract_ids(self, query: str) -> list[tuple[str, str]]:
        """从问题抽显式 KEGG/UniProt 标识（source, asset_id）"""
        found: list[tuple[str, str]] = []
        if self.registry_has("kegg"):
            for raw in _KEGG_ID_RE.findall(query):
                found.append(("kegg", raw.lower()))
        if self.registry_has("uniprot"):
            for acc in _UNIPROT_ACC_RE.findall(query):
                found.append(("uniprot", acc))
        return found

    def kb_working_dir(self) -> Path | None:
        """知识库工作目录；无 working_dir 或类型非法时返回 None。"""
        wd = getattr(self.knowledge_client, "working_dir", None)
        if not wd:
            return None
        try:
            return Path(wd)
        except TypeError:
            return None

    def kb_asset_ids(self) -> set[str] | None:
        """专属文档头里的资产 ID（小写）；无法定位 working_dir 时返回 None。

        仅认 `# KEGG 通路: map…` / `# UniProt 蛋白: P…` 这类入库头，
        不把实体交叉引用当作「已有该通路/蛋白文档」。
        """
        return self.index.asset_ids() if self.index is not None else None

    def kb_entity_names(self) -> set[str] | None:
        """实体表实体名（小写）；用于 genes 是否已在图中"""
        return self.index.entity_names() if self.index is not None else None

    def kb_known_ids(self) -> set[str] | None:
        """兼容旧调用：文档头资产 ID ∪ 实体名（小写）"""
        snapshot = self.index.snapshot() if self.index is not None else None
        if snapshot is None:
            return None
        return snapshot[0] | snapshot[1]

    def context_for_miss_check(self, query: str) -> str | None:
        """取 only_need_context 文本；无法检查时返回 None（跳过补库）"""
        if self.knowledge_client is None:
            return None
        qctx = getattr(self.knowledge_client, "query_context", None)
        if not callable(qctx):
            return None
        try:
            return qctx(query) or ""
        except Exception as e:  # noqa: BLE001 - 检查失败不阻断正常问答
            self.logger.warning("query_context for miss-check failed: %s", e)
            return None

    def ingest_missing(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """从问题抽 KEGG/UniProt 标识并按需入库；失败逐条降级，返回成功列表"""
        if self.builder is None or self.registry is None:
            return []

        candidates = self.collect_candidates(query, params or {})
        ingested: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for source, asset_id in candidates:
            key = (source, asset_id.upper() if source == "uniprot" else asset_id.lower())
            if key in seen:
                continue
            seen.add(key)
            if len(ingested) >= LAZY_INGEST_MAX_ASSETS:
                break
            try:
                result = self.ingest_asset(source, asset_id)
            except Exception as e:  # noqa: BLE001 - 单条失败不阻断后续与回答
                self.logger.warning("lazy ingest %s/%s failed: %s", source, asset_id, e)
                continue
            if result.get("status") == "success":
                ingested.append({"source": source, "asset_id": asset_id, "title": asset_id})
                self.logger.info("lazy ingested %s/%s", source, asset_id)
            else:
                self.logger.warning(
                    "lazy ingest %s/%s: %s", source, asset_id, result.get("message")
                )
        return ingested

    def lazy_ingest(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """兼容旧命名：按需补库。"""
        return self.ingest_missing(query, params)

    def collect_candidates(self, query: str, params: dict[str, Any]) -> list[tuple[str, str]]:
        """显式 ID 优先，不足再 search 兜底；仅 kegg/uniprot，上限 MAX"""
        candidates: list[tuple[str, str]] = []
        max_n = LAZY_INGEST_MAX_ASSETS

        if self.registry_has("kegg"):
            for raw in _KEGG_ID_RE.findall(query):
                candidates.append(("kegg", raw.lower()))
                if len(candidates) >= max_n:
                    return candidates

        if self.registry_has("uniprot"):
            for acc in _UNIPROT_ACC_RE.findall(query):
                candidates.append(("uniprot", acc))
                if len(candidates) >= max_n:
                    return candidates

        # 自由文本 / 基因符号：search 兜底（仍不含 GEO）
        for source in LAZY_INGEST_SOURCES:
            if len(candidates) >= max_n:
                break
            if not self.registry_has(source):
                continue
            fetcher = self.registry.get(source)
            search_q = self.search_term_for(source, query, params)
            if not search_q:
                continue
            try:
                metas = fetcher.search(search_q, max_results=max_n)
            except Exception as e:  # noqa: BLE001
                self.logger.warning("lazy search %s failed: %s", source, e)
                continue
            for meta in metas:
                candidates.append((source, meta.asset_id))
                if len(candidates) >= max_n:
                    break
        return candidates[:max_n]

    def registry_has(self, source: str) -> bool:
        """注册表是否提供 source；兼容 has/sources/get 三种接口。"""
        if self.registry is None:
            return False
        has = getattr(self.registry, "has", None)
        if callable(has):
            try:
                return bool(has(source))
            except Exception:  # noqa: BLE001, S110 - 兼容无 has 的 mock
                pass
        sources = getattr(self.registry, "sources", None)
        if callable(sources):
            try:
                return source in sources()
            except Exception:  # noqa: BLE001
                return False
        try:
            self.registry.get(source)
            return True
        except Exception:  # noqa: BLE001
            return False

    def search_term_for(self, source: str, query: str, params: dict[str, Any]) -> str:
        """search 用词：UniProt 优先 gene:，KEGG 用原问题"""
        genes = params.get("genes") or []
        if source == "uniprot" and genes:
            return " OR ".join(f"gene:{g}" for g in genes[:3])
        # 去掉明显不像检索词的空白
        return query.strip()
