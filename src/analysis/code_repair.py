"""R 代码失败修复（可选 LLM）"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class CodeRepairer:
    """根据 R 报错让 LLM 改写脚本；无 LLM/失败返回 None"""

    def __init__(self, llm_client: Any = None, model: str = "gpt-4o-mini"):
        self.llm_client = llm_client
        self.model = model

    def repair(self, code: str, error: str) -> str | None:
        if not self.llm_client:
            return None
        prompt = (
            "以下是失败的 R 分析脚本与报错。请输出修复后的完整 R 脚本，"
            "只输出代码，不要 Markdown 围栏，不要解释。\n"
            f"## 报错\n{error[:2000]}\n## 脚本\n{code[:4000]}"
        )
        try:
            resp = self.llm_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=2000,
            )
            text = resp.choices[0].message.content or ""
            text = text.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines)
            return text or None
        except Exception as e:  # noqa: BLE001
            logger.warning("code repair failed: %s", e)
            return None
