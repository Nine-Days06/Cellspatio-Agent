"""导出模块 - 支持多组学智能体兼容格式"""
import json
import csv
import sqlite3
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path


class ArticleExporter:
    """文献导出器"""
    
    def __init__(self, export_dir: str = None):
        from config.settings import OUTPUT_DIR
        self.export_dir = Path(export_dir) if export_dir else OUTPUT_DIR / "export"
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
                'keywords': article.get('keywords', '').split('|') if article.get('keywords') else [],
                'mesh_terms': article.get('mesh_terms', '').split('|') if article.get('mesh_terms') else [],
                'authors': article.get('authors', '').split('|') if article.get('authors') else [],
                'year': article.get('pub_year'),
                'journal': article.get('journal', ''),
                'doi': article.get('doi', ''),
                'pmc_id': article.get('pmc_id', ''),
                'export_timestamp': datetime.now().isoformat(),
            }
            export_data.append(export_item)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        print(f"  导出 {len(export_data)} 篇文献到 {output_path}")
        return str(output_path)
    
    def export_to_csv(self, articles: List[Dict[str, Any]], output_path: str = None) -> str:
        """导出为 CSV 格式"""
        if output_path is None:
            output_path = self.export_dir / f"approved_articles_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        fieldnames = ['pmid', 'title', 'abstract', 'keywords', 'mesh_terms', 
                      'authors', 'pub_year', 'journal', 'doi', 'pmc_id',
                      'export_timestamp']
        
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for article in articles:
                row = {
                    'pmid': article.get('pmid', ''),
                    'title': article.get('title', ''),
                    'abstract': article.get('abstract', ''),
                    'keywords': article.get('keywords', ''),
                    'mesh_terms': article.get('mesh_terms', ''),
                    'authors': article.get('authors', ''),
                    'pub_year': article.get('pub_year', ''),
                    'journal': article.get('journal', ''),
                    'doi': article.get('doi', ''),
                    'pmc_id': article.get('pmc_id', ''),
                    'export_timestamp': datetime.now().isoformat(),
                }
                writer.writerow(row)
        
        print(f"  导出 {len(articles)} 篇文献到 {output_path}")
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
                pub_year INTEGER,
                journal TEXT,
                doi TEXT,
                pmc_id TEXT,
                export_timestamp TEXT
            )
        """)
        
        for article in articles:
            cursor.execute("""
                INSERT OR REPLACE INTO articles 
                (pmid, title, abstract, keywords, mesh_terms, authors, pub_year, journal, 
                 doi, pmc_id, export_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                article.get('pmid', ''),
                article.get('title', ''),
                article.get('abstract', ''),
                article.get('keywords', ''),
                article.get('mesh_terms', ''),
                article.get('authors', ''),
                article.get('pub_year'),
                article.get('journal', ''),
                article.get('doi', ''),
                article.get('pmc_id', ''),
                datetime.now().isoformat(),
            ))
        
        conn.commit()
        conn.close()
        print(f"  导出 {len(articles)} 篇文献到 {output_path}")
        return str(output_path)
