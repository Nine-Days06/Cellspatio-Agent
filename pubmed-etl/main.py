"""PubMed ETL 主程序"""
import os
import logging
from typing import List, Dict, Any
from pathlib import Path

from downloader.pubmed_downloader import PubMedDownloader
from cleaner.cleaner import ArticleCleaner
from llm_validator.llm_validator import LLMValidator
from human_review.human_review import HumanReviewer
from export.export import ArticleExporter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """主流程"""
    print("=== PubMed ETL 文献处理工具 ===")
    
    # 1. 下载文献
    print("\n1. 下载 PubMed 文献...")
    downloader = PubMedDownloader()
    search_terms = [
        "multi-omics AND cancer",
        "transcriptomics AND proteomics AND analysis",
        "RNA-seq AND differential expression",
    ]
    all_articles = []
    for term in search_terms:
        pmids = downloader.search(term, max_results=100)
        articles = downloader.fetch_details(pmids)
        all_articles.extend(articles)
    print(f"   下载了 {len(all_articles)} 篇文献")
    
    # 2. 清洗文献
    print("\n2. 清洗文献...")
    cleaner = ArticleCleaner()
    cleaned = cleaner.clean_articles(all_articles)
    print(f"   通过清洗: {len(cleaned['cleaned_articles'])} 篇")
    
    # 3. LLM 验证
    print("\n3. LLM 验证...")
    validator = LLMValidator(provider="openai", api_key=os.getenv("OPENAI_API_KEY"))
    llm_results = validator.batch_validate(cleaned['cleaned_articles'])
    relevant = [r for r in llm_results if r['llm_verdict'] == 'relevant']
    print(f"   LLM 判定相关: {len(relevant)} 篇")
    
    # 4. 人工复核
    print("\n4. 人工复核...")
    reviewer = HumanReviewer()
    review_file = reviewer.export_for_review(cleaned['cleaned_articles'], llm_results)
    print(f"   请复核文件: {review_file}")
    input("   复核完成后按 Enter 继续...")
    
    # 5. 导出
    print("\n5. 导出文献...")
    approved = reviewer.load_reviewed_articles(review_file)
    exporter = ArticleExporter()
    
    json_path = exporter.export_to_json(approved)
    csv_path = exporter.export_to_csv(approved)
    sqlite_path = exporter.export_to_sqlite(approved)
    
    print(f"   JSON: {json_path}")
    print(f"   CSV: {csv_path}")
    print(f"   SQLite: {sqlite_path}")
    print("\n=== 完成 ===")

if __name__ == "__main__":
    main()