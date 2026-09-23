import logging
from typing import Any

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

    def build_from_pubmed(self, search_terms: list[str] | None = None, max_per_term: int = 500) -> dict[str, Any]:
        """弃用：文献唯一通道为 pubmed-etl 产物经 KnowledgeImporter 导入"""
        logger.warning("build_from_pubmed 已弃用：请使用 pubmed-etl 导出 + KnowledgeImporter")
        return {"inserted": 0}

    def build_from_kegg(self, pathway_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 KEGG 通路批量构建"""
        self._need_registry()
        fetcher = fetcher or self.registry.get("kegg")
        texts = [fetcher.ingest_text(pid) for pid in pathway_ids]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from KEGG: {len(pathway_ids)} pathways, inserted {inserted}")
        return {"requested": len(pathway_ids), "inserted": inserted}

    def build_from_uniprot(self, accessions: list[str], fetcher=None) -> dict[str, Any]:
        """从 UniProt 蛋白条目批量构建"""
        self._need_registry()
        fetcher = fetcher or self.registry.get("uniprot")
        texts = [fetcher.ingest_text(acc) for acc in accessions]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from UniProt: {len(accessions)} proteins, inserted {inserted}")
        return {"requested": len(accessions), "inserted": inserted}

    def build_from_geo_metadata(self, gse_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 GEO 数据集元数据批量构建（供「找数据集」类问答）"""
        self._need_registry()
        fetcher = fetcher or self.registry.get("geo")
        texts = [fetcher.ingest_text(gse) for gse in gse_ids]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from GEO metadata: {len(gse_ids)}, inserted {inserted}")
        return {"requested": len(gse_ids), "inserted": inserted}

    def build_from_files(self, file_paths: list[str]) -> dict[str, Any]:
        """从本地文本文件批量构建"""
        texts = []
        for file_path in file_paths:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
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
        texts = [self._convert_article_to_text(a) for a in articles]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from {len(articles)} articles")
        return {"inserted": inserted}

    def build_initial_knowledge_base(self, config: dict[str, Any]) -> dict[str, Any]:
        """按 sources 批量构建初始知识库"""
        sources = config.get('sources', [])
        inserted_total = 0
        handled = []
        for source in sources:
            source_type = source.get('type')
            if source_type == 'pubmed':
                logger.warning("source 'pubmed' 已弃用，请使用 pubmed-etl + KnowledgeImporter")
                continue
            if source_type == 'kegg':
                result = self.build_from_kegg(source.get('pathway_ids', []))
            elif source_type == 'uniprot':
                result = self.build_from_uniprot(source.get('accessions', []))
            elif source_type == 'geo_metadata':
                result = self.build_from_geo_metadata(source.get('gse_ids', []))
            elif source_type == 'files':
                result = self.build_from_files(source.get('file_paths', []))
            else:
                logger.warning(f"未知 source type: {source_type}")
                continue
            inserted_total += result.get("inserted", 0)
            handled.append(source_type)
        logger.info(f"Initial knowledge base built: {inserted_total} documents from {handled}")
        return {"inserted": inserted_total, "sources": handled}

    def _convert_article_to_text(self, article: dict[str, Any]) -> str:
        """将文章转换为 LightRAG 可接受的文本格式"""
        text_parts = []

        if article.get("title"):
            text_parts.append(f"标题：{article['title']}")
        if article.get("abstract"):
            text_parts.append(f"摘要：{article['abstract']}")

        keywords = article.get("keywords", "")
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        elif isinstance(keywords, list):
            pass
        else:
            keywords = []
        if keywords:
            text_parts.append(f"关键词：{', '.join(keywords)}")

        mesh_terms = article.get("mesh_terms", "")
        if isinstance(mesh_terms, str):
            mesh_terms = [m.strip() for m in mesh_terms.split(",") if m.strip()]
        elif isinstance(mesh_terms, list):
            pass
        else:
            mesh_terms = []
        if mesh_terms:
            text_parts.append(f"MeSH词：{', '.join(mesh_terms)}")

        authors = article.get("authors", "")
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(",") if a.strip()]
        elif isinstance(authors, list):
            pass
        else:
            authors = []
        if authors:
            text_parts.append(f"作者：{', '.join(authors)}")

        if article.get("year"):
            text_parts.append(f"年份：{article['year']}")
        if article.get("journal"):
            text_parts.append(f"期刊：{article['journal']}")
        if article.get("omics_type"):
            text_parts.append(f"组学类型：{article['omics_type']}")
        if article.get("pmid"):
            text_parts.append(f"PMID：{article['pmid']}")
            text_parts.append(
                f"链接：https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/"
            )
        if article.get("doi"):
            doi = str(article["doi"]).removeprefix("https://doi.org/")
            text_parts.append(f"DOI：{doi}")

        return "\n".join(text_parts)

    def get_build_statistics(self) -> dict[str, Any]:
        """获取知识库构建统计信息"""
        return {
            "lightrag_initialized": self.client is not None,
        }