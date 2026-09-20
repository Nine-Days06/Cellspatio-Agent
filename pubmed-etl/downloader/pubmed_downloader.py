"""PubMed 文献下载器"""
import os
import time
import requests
import xml.etree.ElementTree as ET
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class PubMedDownloader:
    """PubMed 批量下载器"""
    
    def __init__(self, api_key: str = None, rate_limit: int = 3):
        self.api_key = api_key or os.getenv("PUBMED_API_KEY", "")
        self.rate_limit = rate_limit
        self.base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        self.last_request_time = 0
    
    def _rate_limit(self):
        """速率限制"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < 1.0 / self.rate_limit:
            time.sleep(1.0 / self.rate_limit - time_since_last)
        self.last_request_time = time.time()
    
    def search(self, query: str, max_results: int = 1000) -> List[str]:
        """搜索 PubMed，返回 PMID 列表"""
        self._rate_limit()
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "api_key": self.api_key,
        }
        try:
            response = requests.get(f"{self.base_url}/esearch.fcgi", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def fetch_details(self, pmids: List[str]) -> List[Dict[str, Any]]:
        """批量获取文献详情"""
        if not pmids:
            return []
        # 分批处理（每批最多 200 个）
        batch_size = 200
        all_articles = []
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i:i + batch_size]
            articles = self._fetch_batch(batch)
            all_articles.extend(articles)
        return all_articles
    
    def _fetch_batch(self, pmids: List[str]) -> List[Dict[str, Any]]:
        """获取一批文献详情"""
        self._rate_limit()
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "api_key": self.api_key,
        }
        try:
            response = requests.get(f"{self.base_url}/efetch.fcgi", params=params)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            articles = []
            for article_elem in root.findall(".//PubmedArticle"):
                article = self._parse_article(article_elem)
                if article:
                    articles.append(article)
            return articles
        except Exception as e:
            logger.error(f"Batch fetch failed: {e}")
            return []
    
    def _parse_article(self, article_elem) -> Dict[str, Any]:
        """解析单篇文献"""
        pmid = article_elem.find(".//PMID").text
        title = article_elem.find(".//ArticleTitle").text or ""
        abstract_elem = article_elem.find(".//Abstract")
        abstract = ""
        if abstract_elem is not None:
            abstract_parts = []
            for text_elem in abstract_elem.findall(".//AbstractText"):
                label = text_elem.get("Label", "")
                text = text_elem.text or ""
                if label:
                    abstract_parts.append(f"{label}: {text}")
                else:
                    abstract_parts.append(text)
            abstract = " ".join(abstract_parts)
        keywords = [kw.text for kw in article_elem.findall(".//Keyword") if kw.text]
        mesh_terms = [m.text for m in article_elem.findall(".//MeshHeading/DescriptorName") if m.text]
        authors = []
        for author_elem in article_elem.findall(".//Author"):
            last_name = author_elem.find("LastName")
            first_name = author_elem.find("ForeName")
            if last_name is not None:
                name = last_name.text
                if first_name is not None:
                    name = f"{first_name.text} {name}"
                authors.append(name)
        pub_date = article_elem.find(".//PubDate")
        year = None
        if pub_date is not None:
            year_elem = pub_date.find("Year")
            if year_elem is not None:
                year = int(year_elem.text)
        journal = article_elem.find(".//Journal/Title")
        journal_name = journal.text if journal is not None else ""
        return {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "keywords": keywords,
            "mesh_terms": mesh_terms,
            "authors": authors,
            "year": year,
            "journal": journal_name,
        }