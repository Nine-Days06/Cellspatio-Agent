import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class KnowledgeBuilder:
    """知识库构建器，从多源数据构建知识库"""
    
    def __init__(self, lightrag_client):
        self.client = lightrag_client
    
    def build_from_pubmed(self, search_terms: List[str] = None, max_per_term: int = 500):
        """从 PubMed 构建知识库（占位符实现）"""
        logger.info(f"Building knowledge from PubMed with terms: {search_terms}")
        # 占位符实现 - 实际需要集成 PubMed API
        return {
            "total_downloaded": 0,
            "cleaned": 0,
            "inserted": 0,
        }
    
    def build_from_kegg(self, pathway_ids: List[str]):
        """从 KEGG 构建知识库（占位符实现）"""
        logger.info(f"Building knowledge from KEGG: {pathway_ids}")
        for pathway_id in pathway_ids:
            document = f"KEGG pathway {pathway_id} information"
            self.client.insert_document(document)
    
    def build_from_files(self, file_paths: List[str]):
        """从本地文件构建知识库"""
        for file_path in file_paths:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    self.client.insert_document(content)
                    logger.info(f"Built knowledge from file: {file_path}")
            except (OSError, UnicodeDecodeError) as e:
                logger.error(f"Failed to process file {file_path}: {e}")
    
    def build_from_text(self, text_content: str, metadata: Dict[str, Any] = None):
        """从文本内容构建知识库"""
        self.client.insert_document(text_content)
        logger.info(f"Built knowledge from text, length: {len(text_content)}")
    
    def build_from_articles(self, articles: List[Dict[str, Any]]):
        """从文章列表构建知识库"""
        for article in articles:
            text_content = self._convert_article_to_text(article)
            self.client.insert_document(text_content)
        logger.info(f"Built knowledge from {len(articles)} articles")
    
    def build_initial_knowledge_base(self, config: Dict[str, Any]):
        """构建初始知识库"""
        sources = config.get('sources', [])
        for source in sources:
            source_type = source.get('type')
            if source_type == 'pubmed':
                search_terms = source.get('search_terms', [])
                max_per_term = source.get('max_per_term', 500)
                self.build_from_pubmed(search_terms, max_per_term)
            elif source_type == 'kegg':
                self.build_from_kegg(source.get('pathway_ids', []))
            elif source_type == 'files':
                self.build_from_files(source.get('file_paths', []))
    
    def _convert_article_to_text(self, article: Dict[str, Any]) -> str:
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
        
        return "\n".join(text_parts)
    
    def _identify_omics_type(self, article: Dict[str, Any]) -> str:
        """识别文献涉及的组学类型"""
        text = f"{article.get('title', '')} {article.get('abstract', '')}".lower()
        
        omics_types = []
        
        if any(kw in text for kw in ["transcriptomics", "rna-seq", "gene expression"]):
            omics_types.append("转录组学")
        
        if any(kw in text for kw in ["proteomics", "mass spectrometry", "protein"]):
            omics_types.append("蛋白质组学")
        
        if any(kw in text for kw in ["metabolomics", "metabolite"]):
            omics_types.append("代谢组学")
        
        if any(kw in text for kw in ["epigenomics", "methylation", "chromatin"]):
            omics_types.append("表观基因组学")
        
        if any(kw in text for kw in ["genomics", "wgs", "wes", "variant"]):
            omics_types.append("基因组学")
        
        if any(kw in text for kw in ["multi-omics", "multiomics", "integrative"]):
            omics_types.append("多组学整合")
        
        return ", ".join(omics_types) if omics_types else "未分类"
    
    def get_build_statistics(self) -> Dict[str, Any]:
        """获取知识库构建统计信息"""
        return {
            "lightrag_initialized": self.client is not None,
        }
