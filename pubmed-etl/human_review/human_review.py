"""人工复核模块"""
import csv
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class HumanReviewer:
    """人工复核管理器"""
    
    def __init__(self, output_dir: str = "data/output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def export_for_review(self, articles: List[Dict[str, Any]], llm_results: List[Dict[str, Any]]) -> str:
        """导出待人工复核的文献"""
        llm_dict = {r["pmid"]: r for r in llm_results}
        merged_data = []
        for article in articles:
            pmid = article.get("pmid")
            llm_result = llm_dict.get(pmid, {})
            merged_data.append({
                "pmid": pmid,
                "title": article.get("title", ""),
                "abstract": article.get("abstract", "")[:500],
                "keywords": "; ".join(article.get("keywords", [])),
                "year": article.get("year", ""),
                "llm_verdict": llm_result.get("llm_verdict", ""),
                "llm_reason": llm_result.get("llm_reason", ""),
                "llm_relevance_score": llm_result.get("llm_relevance_score", 0.0),
                "human_review": "",
                "human_notes": "",
            })
        
        output_file = self.output_dir / "articles_for_review.csv"
        df = pd.DataFrame(merged_data)
        df.to_csv(output_file, index=False, encoding='utf-8')
        logger.info(f"Exported {len(merged_data)} articles for review to {output_file}")
        return str(output_file)
    
    def load_reviewed_articles(self, review_file: str) -> List[Dict[str, Any]]:
        """加载已复核的文献"""
        df = pd.read_csv(review_file)
        approved = df[df['human_review'].str.upper() == 'Y']
        return approved.to_dict('records')