"""导出配置 - 与人类多组学分析智能体兼容"""
from dataclasses import dataclass
from typing import List
from pathlib import Path

@dataclass
class ExportConfig:
    """导出配置"""
    # 支持的导出格式
    supported_formats: List[str] = None
    
    # 默认导出格式
    default_format: str = "json"
    
    # 导出目录
    export_dir: str = "data/export"
    
    # JSON 导出配置
    json_indent: int = 2
    json_ensure_ascii: bool = False
    
    # CSV 导出配置
    csv_encoding: str = "utf-8"
    
    # SQLite 导出配置
    sqlite_db_name: str = "approved_articles.db"
    
    # 导出字段
    export_fields: List[str] = None
    
    def __post_init__(self):
        if self.supported_formats is None:
            self.supported_formats = ['json', 'csv', 'sqlite']
        
        if self.export_fields is None:
            self.export_fields = [
                'pmid', 'title', 'abstract', 'keywords', 
                'mesh_terms', 'authors', 'year', 'journal',
                'human_review', 'llm_verdict', 'llm_relevance_score'
            ]
        
        Path(self.export_dir).mkdir(parents=True, exist_ok=True)

# 全局配置实例
export_config = ExportConfig()