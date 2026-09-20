"""导出模块 - 支持多组学智能体兼容格式"""
import json
import csv
import sqlite3
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class ArticleExporter:
    """文献导出器"""
    
    def __init__(self, export_dir: str = "data/export"):
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
    
    def export_to_json(self, articles: List[Dict[str, Any]], output_path: str = None) -> str:
        """导出为 JSON 格式"""
        if output_path is None:
            output_path = self.export_dir / f"approved_articles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        export_data = []
        for article in articles:
            export_item = {
                'pmid': article.get('pmid', ''),
                'title': article.get('title', ''),
                'abstract': article.get('abstract', ''),
                'keywords': article.get('keywords', []) if isinstance(article.get('keywords'), list) else [],
                'mesh_terms': article.get('mesh_terms', []) if isinstance(article.get('mesh_terms'), list) else [],
                'authors': article.get('authors', []) if isinstance(article.get('authors'), list) else [],
                'year': article.get('year'),
                'journal': article.get('journal', ''),
                'human_review': article.get('human_review', ''),
                'llm_verdict': article.get('llm_verdict', ''),
                'llm_relevance_score': article.get('llm_relevance_score', 0.0),
                'export_timestamp': datetime.now().isoformat(),
            }
            export_data.append(export_item)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Exported {len(export_data)} articles to {output_path}")
        return str(output_path)
    
    def export_to_csv(self, articles: List[Dict[str, Any]], output_path: str = None) -> str:
        """导出为 CSV 格式"""
        if output_path is None:
            output_path = self.export_dir / f"approved_articles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        fieldnames = ['pmid', 'title', 'abstract', 'keywords', 'mesh_terms', 
                      'authors', 'year', 'journal', 'human_review', 'llm_verdict', 
                      'llm_relevance_score', 'export_timestamp']
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for article in articles:
                row = {
                    'pmid': article.get('pmid', ''),
                    'title': article.get('title', ''),
                    'abstract': article.get('abstract', ''),
                    'keywords': '; '.join(article.get('keywords', [])) if isinstance(article.get('keywords'), list) else '',
                    'mesh_terms': '; '.join(article.get('mesh_terms', [])) if isinstance(article.get('mesh_terms'), list) else '',
                    'authors': '; '.join(article.get('authors', [])) if isinstance(article.get('authors'), list) else '',
                    'year': article.get('year', ''),
                    'journal': article.get('journal', ''),
                    'human_review': article.get('human_review', ''),
                    'llm_verdict': article.get('llm_verdict', ''),
                    'llm_relevance_score': article.get('llm_relevance_score', 0.0),
                    'export_timestamp': datetime.now().isoformat(),
                }
                writer.writerow(row)
        logger.info(f"Exported {len(articles)} articles to {output_path}")
        return str(output_path)
    
    def export_to_sqlite(self, articles: List[Dict[str, Any]], output_path: str = None) -> str:
        """导出为 SQLite 格式"""
        if output_path is None:
            output_path = self.export_dir / f"approved_articles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        conn = sqlite3.connect(output_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                pmid TEXT PRIMARY KEY,
                title TEXT,
                abstract TEXT,
                keywords TEXT,
                mesh_terms TEXT,
                authors TEXT,
                year INTEGER,
                journal TEXT,
                human_review TEXT,
                llm_verdict TEXT,
                llm_relevance_score REAL,
                export_timestamp TEXT
            )
        """)
        for article in articles:
            cursor.execute("""
                INSERT OR REPLACE INTO articles 
                (pmid, title, abstract, keywords, mesh_terms, authors, year, journal, 
                 human_review, llm_verdict, llm_relevance_score, export_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                article.get('pmid', ''),
                article.get('title', ''),
                article.get('abstract', ''),
                '; '.join(article.get('keywords', [])) if isinstance(article.get('keywords'), list) else '',
                '; '.join(article.get('mesh_terms', [])) if isinstance(article.get('mesh_terms'), list) else '',
                '; '.join(article.get('authors', [])) if isinstance(article.get('authors'), list) else '',
                article.get('year'),
                article.get('journal', ''),
                article.get('human_review', ''),
                article.get('llm_verdict', ''),
                article.get('llm_relevance_score', 0.0),
                datetime.now().isoformat(),
            ))
        conn.commit()
        conn.close()
        logger.info(f"Exported {len(articles)} articles to {output_path}")
        return str(output_path)