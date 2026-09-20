"""文献清洗模块"""
from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)

class ArticleCleaner:
    """文献清洗器，支持硬过滤和多组学相关性判断"""
    
    def __init__(self, config=None):
        self.config = config or self._get_default_config()
    
    def _get_default_config(self):
        return {
            "min_year": 2015,
            "max_year": 2026,
            "allowed_languages": ["eng", "chi"],
            "omics_keywords": [
                "multi-omics", "transcriptomics", "proteomics",
                "metabolomics", "epigenomics", "genomics",
                "RNA-seq", "ChIP-seq", "ATAC-seq", "mass spectrometry",
                "single-cell", "spatial transcriptomics",
            ],
        }
    
    def hard_filter(self, articles: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """硬过滤：返回 (通过的文献, 被过滤的文献)"""
        passed = []
        filtered = []
        for article in articles:
            reasons = self._check_article(article)
            if not reasons:
                passed.append(article)
            else:
                article["filter_reasons"] = reasons
                filtered.append(article)
        return passed, filtered
    
    def _check_article(self, article: Dict[str, Any]) -> List[str]:
        """检查文章是否应该被过滤"""
        reasons = []
        year = article.get("year")
        if year:
            if year < self.config["min_year"]:
                reasons.append(f"年份过早: {year}")
            elif year > self.config["max_year"]:
                reasons.append(f"年份过晚: {year}")
        abstract = article.get("abstract", "")
        if not abstract or len(abstract) < 100:
            reasons.append("摘要过短或缺失")
        title = article.get("title", "")
        if not title:
            reasons.append("标题缺失")
        return reasons
    
    def is_multiomics_relevant(self, article: Dict[str, Any]) -> bool:
        """判断文章是否与多组学相关"""
        title = article.get("title", "").lower()
        for keyword in self.config["omics_keywords"]:
            if keyword.lower() in title:
                return True
        abstract = article.get("abstract", "").lower()
        for keyword in self.config["omics_keywords"]:
            if keyword.lower() in abstract:
                return True
        keywords = article.get("keywords", [])
        for kw in keywords:
            for keyword in self.config["omics_keywords"]:
                if keyword.lower() in kw.lower():
                    return True
        return False
    
    def clean_articles(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """完整清洗流程"""
        passed, hard_filtered = self.hard_filter(articles)
        relevant = [a for a in passed if self.is_multiomics_relevant(a)]
        irrelevant = [a for a in passed if not self.is_multiomics_relevant(a)]
        return {
            "cleaned_articles": relevant,
            "hard_filtered": hard_filtered,
            "irrelevant": irrelevant,
            "stats": {
                "total": len(articles),
                "passed_hard_filter": len(passed),
                "relevant": len(relevant),
            }
        }