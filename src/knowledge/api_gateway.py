import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

class APIGateway:
    """外部 API 网关，封装多个公共数据库 API"""
    
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.enabled = self.config.get('enabled', False)
        self.rate_limits = {}
    
    async def query_pubmed(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        """查询 PubMed API"""
        if not self.enabled:
            logger.warning("External API disabled")
            return []
        
        # 简化示例，实际需要完整的 PubMed API 调用
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json"
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("esearchresult", {}).get("idlist", [])
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"PubMed API error: {e}")
        
        return []
    
    async def query_kegg(self, pathway_id: str) -> dict[str, Any]:
        """查询 KEGG API"""
        if not self.enabled:
            return {}
        
        # 简化示例
        url = f"https://rest.kegg.jp/get/{pathway_id}"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return {"pathway_id": pathway_id, "data": response.text}
        except (httpx.RequestError, ValueError) as e:
            logger.error(f"KEGG API error: {e}")
        
        return {}
    
    def set_enabled(self, enabled: bool):
        """启用/禁用外部 API"""
        self.enabled = enabled
        logger.info(f"External API {'enabled' if enabled else 'disabled'}")
