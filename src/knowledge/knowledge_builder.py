import logging
from pathlib import Path
from typing import Any

from src.knowledge.article_text import convert_article_to_text

logger = logging.getLogger(__name__)


class KnowledgeBuilder:
    """知识库构建器，从多源数据批量构建知识库

    文献走 KnowledgeImporter（pubmed-etl 产物）；
    公共数据库走 data/fetchers（KEGG / UniProt / GEO 元数据）。
    """

    def __init__(self, lightrag_client, fetcher_registry=None):
        self.client = lightrag_client
        self.registry = fetcher_registry

    def _need_registry(self):
        if self.registry is None:
            from src.data.registry import FetcherRegistry

            self.registry = FetcherRegistry.build_default()

    def _insert_texts(self, texts: list[str]) -> int:
        return self.client.insert_documents(texts)

    def _build_fetched(
        self,
        label: str,
        ids: list[str],
        source: str,
        noun: str,
        fetcher_name: str,
        fetcher: Any = None,
    ) -> dict[str, Any]:
        """按抓取源批量生成文本并入库，统一统计与日志。"""
        self._need_registry()
        fetcher = fetcher or self.registry.get(fetcher_name)
        texts = [getattr(fetcher, source)(item) for item in ids]
        inserted = self._insert_texts(texts)
        count = f"{len(ids)} {noun}".strip()
        logger.info(f"Built knowledge from {label}: {count}, inserted {inserted}")
        return {"requested": len(ids), "inserted": inserted}

    def _build_texts(self, label: str, texts: list[str], noun: str = "docs") -> dict[str, Any]:
        """把已生成的文本批量入库。"""
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from {label}: {len(texts)} {noun}, inserted {inserted}")
        return {"requested": len(texts), "inserted": inserted}

    def build_from_pubmed(
        self, search_terms: list[str] | None = None, max_per_term: int = 500
    ) -> dict[str, Any]:
        """弃用：文献唯一通道为 pubmed-etl 产物经 KnowledgeImporter 导入"""
        logger.warning("build_from_pubmed 已弃用：请使用 pubmed-etl 导出 + KnowledgeImporter")
        return {"inserted": 0}

    def build_from_kegg(self, pathway_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 KEGG 通路批量构建"""
        return self._build_fetched("KEGG", pathway_ids, "ingest_text", "pathways", "kegg", fetcher)

    def build_from_uniprot(self, accessions: list[str], fetcher=None) -> dict[str, Any]:
        """从 UniProt 蛋白条目批量构建"""
        return self._build_fetched(
            "UniProt", accessions, "ingest_text", "proteins", "uniprot", fetcher
        )

    def build_from_kegg_kgml(self, pathway_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 KEGG 通路 KGML 结构批量构建"""
        return self._build_fetched(
            "KEGG KGML", pathway_ids, "ingest_kgml_text", "pathways", "kegg", fetcher
        )

    def build_from_reactome(self, st_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 Reactome 通路批量构建"""
        return self._build_fetched(
            "Reactome", st_ids, "ingest_text", "pathways", "reactome", fetcher
        )

    def build_from_hpa(self, genes: list[str], fetcher=None) -> dict[str, Any]:
        """从 HPA 蛋白条目批量构建"""
        return self._build_fetched("HPA", genes, "ingest_text", "proteins", "hpa", fetcher)

    def build_from_geo_metadata(self, gse_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 GEO 数据集元数据批量构建（供「找数据集」类问答）"""
        return self._build_fetched("GEO metadata", gse_ids, "ingest_text", "", "geo", fetcher)

    def build_from_cellmarker(self, xlsx_path: str | None = None) -> dict[str, Any]:
        """从 CellMarker 人类细胞标志物表批量构建"""
        from src.knowledge.curated_sources import cellmarker_texts

        texts = cellmarker_texts(Path(xlsx_path) if xlsx_path else None)
        return self._build_texts("CellMarker", texts)

    def build_from_cell_ontology(self, obo_path: str | None = None) -> dict[str, Any]:
        """从 Cell Ontology cl.obo 批量构建"""
        from src.knowledge.curated_sources import cell_ontology_texts

        texts = cell_ontology_texts(Path(obo_path) if obo_path else None)
        return self._build_texts("Cell Ontology", texts)

    def build_from_files(self, file_paths: list[str]) -> dict[str, Any]:
        """从本地文本文件批量构建"""
        texts = []
        for file_path in file_paths:
            try:
                with open(file_path, encoding="utf-8") as f:
                    texts.append(f.read())
            except (OSError, UnicodeDecodeError) as e:
                logger.error(f"Failed to process file {file_path}: {e}")
        inserted = self._insert_texts(texts)
        return {"requested": len(file_paths), "inserted": inserted}

    def build_from_text(self, text_content: str, metadata: dict[str, Any] | None = None):
        """从单段文本构建"""
        inserted = self._insert_texts([text_content])
        logger.info(f"Built knowledge from text, length: {len(text_content)}")
        return {"inserted": inserted}

    def build_from_articles(self, articles: list[dict[str, Any]]) -> dict[str, Any]:
        """从文章列表批量构建"""
        texts = [convert_article_to_text(a) for a in articles]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from {len(articles)} articles")
        return {"inserted": inserted}

    def build_initial_knowledge_base(self, config: dict[str, Any]) -> dict[str, Any]:
        """按 sources 批量构建初始知识库"""
        sources = config.get("sources", [])
        inserted_total = 0
        handled = []
        for source in sources:
            source_type = source.get("type")
            if source_type == "pubmed":
                logger.warning("source 'pubmed' 已弃用，请使用 pubmed-etl + KnowledgeImporter")
                continue
            if source_type == "kegg":
                result = self.build_from_kegg(source.get("pathway_ids", []))
            elif source_type == "uniprot":
                result = self.build_from_uniprot(source.get("accessions", []))
            elif source_type == "geo_metadata":
                result = self.build_from_geo_metadata(source.get("gse_ids", []))
            elif source_type == "files":
                result = self.build_from_files(source.get("file_paths", []))
            else:
                logger.warning(f"未知 source type: {source_type}")
                continue
            inserted_total += result.get("inserted", 0)
            handled.append(source_type)
        logger.info(f"Initial knowledge base built: {inserted_total} documents from {handled}")
        return {"inserted": inserted_total, "sources": handled}

    def get_build_statistics(self) -> dict[str, Any]:
        """获取知识库构建统计信息"""
        return {
            "lightrag_initialized": self.client is not None,
        }
