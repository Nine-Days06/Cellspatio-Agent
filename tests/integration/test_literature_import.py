import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.knowledge.knowledge_importer import KnowledgeImporter


class MockLightRAGClient:
    """模拟 LightRAG 客户端"""
    def __init__(self):
        self.inserted_documents = []
        self.insert_count = 0
    
    def insert_document(self, document: str):
        self.inserted_documents.append(document)
        self.insert_count += 1
    
    def insert_documents(self, documents: list[str]) -> int:
        for doc in documents:
            self.insert_document(doc)
        return len(documents)

@pytest.fixture
def mock_client():
    return MockLightRAGClient()

@pytest.fixture
def sample_json_file():
    """创建示例 JSON 文件"""
    data = [
        {
            "pmid": "12345678",
            "title": "Multi-omics analysis of cancer",
            "abstract": "This study presents a comprehensive multi-omics analysis...",
            "keywords": ["multi-omics", "cancer", "proteomics"],
            "mesh_terms": ["Neoplasms", "Proteomics"],
            "authors": ["Zhang Y", "Li X"],
            "year": 2024,
            "journal": "Nature Communications",
            "human_review": "Y",
            "llm_verdict": "relevant",
            "llm_relevance_score": 0.95
        },
        {
            "pmid": "87654321",
            "title": "RNA-seq analysis",
            "abstract": "This study performs RNA-seq analysis...",
            "keywords": ["RNA-seq", "transcriptomics"],
            "mesh_terms": ["RNA", "Transcriptome"],
            "authors": ["Wang Z"],
            "year": 2023,
            "journal": "Cell Reports",
            "human_review": "Y",
            "llm_verdict": "relevant",
            "llm_relevance_score": 0.88
        }
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        return f.name

@pytest.fixture
def sample_csv_file():
    """创建示例 CSV 文件"""
    csv_content = """pmid,title,abstract,keywords,mesh_terms,authors,year,journal,human_review,llm_verdict,llm_relevance_score
12345678,"Multi-omics analysis","This study presents...","multi-omics;cancer","Neoplasms;Proteomics","Zhang Y;Li X",2024,"Nature Communications","Y","relevant",0.95
87654321,"RNA-seq analysis","This study performs...","RNA-seq;transcriptomics","RNA;Transcriptome","Wang Z",2023,"Cell Reports","Y","relevant",0.88"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(csv_content)
        return f.name

def test_import_json(mock_client, sample_json_file):
    """测试 JSON 导入"""
    importer = KnowledgeImporter(mock_client)
    
    result = importer.import_from_json(sample_json_file)
    
    assert result["success"] == True
    assert result["count"] == 2
    assert mock_client.insert_count == 2
    
    # 清理
    os.unlink(sample_json_file)

def test_import_csv(mock_client, sample_csv_file):
    """测试 CSV 导入"""
    importer = KnowledgeImporter(mock_client)
    
    result = importer.import_from_csv(sample_csv_file)
    
    assert result["success"] == True
    assert result["count"] == 2
    assert mock_client.insert_count == 2
    
    # 清理
    os.unlink(sample_csv_file)

def test_import_directory(mock_client, sample_json_file, sample_csv_file):
    """测试目录批量导入"""
    importer = KnowledgeImporter(mock_client)
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        # 复制文件到临时目录
        import shutil
        shutil.copy(sample_json_file, tmpdir)
        shutil.copy(sample_csv_file, tmpdir)
        
        result = importer.import_from_directory(tmpdir)
        
        assert result["success"] == True
        assert result["total_count"] == 4  # 2 from JSON + 2 from CSV
    
    # 清理
    os.unlink(sample_json_file)
    os.unlink(sample_csv_file)

def test_article_to_text_conversion(mock_client):
    """测试文章转文本格式"""
    importer = KnowledgeImporter(mock_client)
    
    article = {
        "pmid": "12345678",
        "title": "Test Article",
        "abstract": "Test abstract",
        "keywords": ["test", "multi-omics"],
        "mesh_terms": ["Test", "Multi-omics"],
        "authors": ["Author One", "Author Two"],
        "year": 2024,
        "journal": "Test Journal"
    }
    
    text = importer._convert_article_to_text(article)
    
    assert "标题：Test Article" in text
    assert "摘要：Test abstract" in text
    assert "关键词：test, multi-omics" in text
    assert "MeSH词：Test, Multi-omics" in text
    assert "作者：Author One, Author Two" in text
    assert "年份：2024" in text
    assert "期刊：Test Journal" in text
