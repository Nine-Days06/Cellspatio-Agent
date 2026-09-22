import logging
from typing import Any

logger = logging.getLogger(__name__)


class ResultExplainer:
    """将统计结果转化为自然语言解释（可选 LLM + 知识库背景）"""

    def __init__(self, llm_client=None, knowledge_client=None):
        self.llm_client = llm_client
        self.knowledge_client = knowledge_client

    def explain_differential_expression(self, results: dict[str, Any]) -> str:
        """解释差异表达分析结果（规则模板）"""
        total_genes = results.get("total_genes", 0)
        significant_genes = results.get("significant_genes", 0)

        percentage = (significant_genes / total_genes * 100) if total_genes > 0 else 0.0

        return f"""
差异表达分析完成。

统计摘要：
- 总基因数：{total_genes}
- 显著差异基因（调整后p值 < 0.05）：{significant_genes}
- 显著比例：{percentage:.1f}%

主要发现：
1. 识别出 {significant_genes} 个在不同条件下表达水平显著变化的基因。
2. 这些基因可能与研究的生物学过程密切相关。

建议下一步：
- 对显著差异基因进行功能富集分析。
- 验证关键基因的表达变化。
- 结合文献进一步解读生物学意义。
"""

    def explain_pathway_analysis(self, pathways: list[dict[str, Any]]) -> str:
        """解释通路分析结果"""
        if not pathways:
            return "未发现显著富集的通路。"

        explanation = "通路富集分析结果：\n\n"
        for i, pathway in enumerate(pathways[:5], 1):
            explanation += f"{i}. {pathway.get('name', 'Unknown')}\n"
            explanation += f"   - 富集分数：{pathway.get('score', 'N/A')}\n"
            explanation += f"   - p值：{pathway.get('pvalue', 'N/A')}\n\n"

        return explanation

    def _knowledge_background(self, data: dict[str, Any]) -> str:
        """查询主知识库，取通路/问题背景；无库或失败返回空串"""
        if self.knowledge_client is None:
            return ""
        pathways = data.get("top_pathways") or []
        question = " ".join(str(p) for p in pathways[:3]) or "多组学差异分析 结果解读"
        try:
            return self.knowledge_client.query(question)
        except Exception as e:  # noqa: BLE001 - 背景是增强，失败降级
            logger.warning("knowledge background query failed: %s", e)
            return ""

    def generate_llm_explanation(self, data: dict[str, Any], question: str) -> str:
        """LLM + 知识库背景生成解说；无 LLM 或调用失败回退规则模板"""
        if not self.llm_client:
            return self.explain_differential_expression(data)

        background = self._knowledge_background(data)
        user_prompt = (
            f"问题：{question}\n"
            f"分析结果摘要：{data}\n"
            f"知识库背景：{background or '（无）'}\n"
            "请用中文给实验生物学家 3-5 句解读：关键发现、注意事项、下一步建议。"
            "只依据摘要与背景，不要编造具体数值。"
        )
        try:
            response = self.llm_client.chat.completions.create(
                model=getattr(self, "model", "gpt-4o-mini"),
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.2,
                max_tokens=500,
            )
            text = response.choices[0].message.content or ""
            if text and text.strip():
                return text.strip()
            return self.explain_differential_expression(data)
        except Exception as e:  # noqa: BLE001 - 解说失败不阻断主流程
            logger.warning("LLM explanation failed, fallback: %s", e)
            return self.explain_differential_expression(data)
