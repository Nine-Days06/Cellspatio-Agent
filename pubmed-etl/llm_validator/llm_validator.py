"""LLM 文献验证模块"""
import json
import time
from typing import List, Dict, Any
import logging
import httpx

logger = logging.getLogger(__name__)

class LLMValidator:
    """LLM 文献验证器"""
    
    def __init__(self, provider: str = "openai", api_key: str = None):
        self.provider = provider
        self.api_key = api_key
        self.api_urls = {
            "openai": "https://api.openai.com/v1/chat/completions",
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        }
        self.api_url = self.api_urls.get(provider, self.api_urls["openai"])
    
    def validate_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """使用 LLM 验证单篇文献"""
        prompt = self._create_validation_prompt(article)
        try:
            response = self._call_llm(prompt)
            result = self._parse_llm_response(response)
            return {
                "pmid": article.get("pmid"),
                "llm_verdict": result.get("verdict", "unknown"),
                "llm_reason": result.get("reason", ""),
                "llm_relevance_score": result.get("relevance_score", 0.0),
                "llm_timestamp": time.time(),
            }
        except Exception as e:
            logger.error(f"LLM validation failed: {e}")
            return {
                "pmid": article.get("pmid"),
                "llm_verdict": "error",
                "llm_reason": str(e),
                "llm_relevance_score": 0.0,
                "llm_timestamp": time.time(),
            }
    
    def _create_validation_prompt(self, article: Dict[str, Any]) -> str:
        return f"""你是一位多组学研究专家。请判断以下文献是否与多组学分析相关。

文献信息：
标题：{article.get('title', '')}
摘要：{article.get('abstract', '')}
关键词：{', '.join(article.get('keywords', []))}

请返回 JSON 格式的评估结果：
{{"verdict": "relevant/irrelevant", "reason": "理由", "relevance_score": 0.0-1.0}}"""
    
    def _call_llm(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": self._get_model_name(),
            "messages": [
                {"role": "system", "content": "你是一位专业的多组学研究分析助手。"},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 1000,
        }
        with httpx.Client(timeout=60.0) as client:
            response = client.post(self.api_url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
    
    def _get_model_name(self) -> str:
        model_names = {
            "openai": "gpt-4",
            "deepseek": "deepseek-chat",
            "zhipu": "glm-4",
        }
        return model_names.get(self.provider, "gpt-4")
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start != -1 and json_end != -1:
                json_str = response[json_start:json_end]
                return json.loads(json_str)
            else:
                return {"verdict": "unknown", "reason": response, "relevance_score": 0.0}
        except json.JSONDecodeError:
            return {"verdict": "unknown", "reason": response, "relevance_score": 0.0}
    
    def batch_validate(self, articles: List[Dict[str, Any]], batch_size: int = 5) -> List[Dict[str, Any]]:
        """批量验证文献"""
        results = []
        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]
            for article in batch:
                result = self.validate_article(article)
                results.append(result)
                time.sleep(1)
        return results