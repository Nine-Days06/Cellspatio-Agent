"""知识库导入模块 - 从独立文献处理项目导入数据"""
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class KnowledgeImporter:
    """知识库导入器，支持多种格式导入"""
    
    def __init__(self, lightrag_client):
        self.client = lightrag_client
    
    def import_from_json(self, json_path: str) -> dict[str, Any]:
        """从 JSON 文件导入知识库"""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            articles = data if isinstance(data, list) else data.get("articles", [])
            
            texts = [self._convert_article_to_text(a) for a in articles]
            count = self.client.insert_documents(texts)
            
            logger.info(f"Imported {count} articles from JSON: {json_path}")
            return {"success": True, "count": count, "source": json_path}
            
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to import from JSON: {e}")
            return {"success": False, "error": str(e)}
    
    def import_from_sqlite(self, db_path: str, query: str | None = None) -> dict[str, Any]:
        """从 SQLite 数据库导入知识库"""
        try:
            conn = sqlite3.connect(db_path)
            
            if query is None:
                query = "SELECT * FROM articles WHERE human_review = 'Y' OR human_review IS NULL"
            
            df = pd.read_sql_query(query, conn)
            conn.close()
            
            texts = [self._convert_article_to_text(row.to_dict()) for _, row in df.iterrows()]
            count = self.client.insert_documents(texts)
            
            logger.info(f"Imported {count} articles from SQLite: {db_path}")
            return {"success": True, "count": count, "source": db_path}
            
        except (FileNotFoundError, sqlite3.Error, pd.errors.DatabaseError) as e:
            logger.error(f"Failed to import from SQLite: {e}")
            return {"success": False, "error": str(e)}
    
    def import_from_csv(self, csv_path: str) -> dict[str, Any]:
        """从 CSV 文件导入知识库"""
        try:
            df = pd.read_csv(csv_path)
            
            texts = [self._convert_article_to_text(row.to_dict()) for _, row in df.iterrows()]
            count = self.client.insert_documents(texts)
            
            logger.info(f"Imported {count} articles from CSV: {csv_path}")
            return {"success": True, "count": count, "source": csv_path}
            
        except (FileNotFoundError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            logger.error(f"Failed to import from CSV: {e}")
            return {"success": False, "error": str(e)}
    
    def import_from_directory(self, dir_path: str, file_types: list[str] | None = None) -> dict[str, Any]:
        """从目录批量导入"""
        if file_types is None:
            file_types = ['json', 'csv']
        
        dir_path = Path(dir_path)
        total_count = 0
        imported_files = []
        
        for file_type in file_types:
            if file_type == 'json':
                pattern = "*.json"
            elif file_type == 'csv':
                pattern = "*.csv"
            elif file_type == 'sqlite':
                pattern = "*.db"
            else:
                continue
            
            for file_path in dir_path.glob(pattern):
                result = self.import_from_file(str(file_path))
                if result.get("success"):
                    total_count += result.get("count", 0)
                    imported_files.append(str(file_path))
        
        logger.info(f"Imported {total_count} articles from {len(imported_files)} files")
        return {"success": True, "total_count": total_count, "imported_files": imported_files}
    
    def import_from_file(self, file_path: str) -> dict[str, Any]:
        """根据文件类型自动选择导入方法"""
        file_path = Path(file_path)
        
        if file_path.suffix.lower() == '.json':
            return self.import_from_json(str(file_path))
        elif file_path.suffix.lower() == '.csv':
            return self.import_from_csv(str(file_path))
        elif file_path.suffix.lower() in ['.db', '.sqlite']:
            return self.import_from_sqlite(str(file_path))
        else:
            return {"success": False, "error": f"Unsupported file type: {file_path.suffix}"}
    
    def _convert_article_to_text(self, article: dict[str, Any]) -> str:
        """将文献转换为 LightRAG 可接受的文本格式"""
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