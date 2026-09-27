"""知识问答技能：真实委派 KGQuery（工作线程内执行，规避嵌套事件循环）。"""
import asyncio

from src.control.kg_query import KGQuery
from src.skills.base import SkillBase, SkillContext
from src.skills.manifest import SkillMetadata


class QueryKnowledgeSkill(SkillBase):
    metadata = SkillMetadata(
        name="query_knowledge",
        version="1.0.0",
        description="基于 LightRAG 知识库的自然语言问答",
        author="CellSpatio",
        tags=["knowledge", "rag"],
    )

    async def execute(self, context: SkillContext) -> dict:
        question = context.params.get("user_input", "").strip()
        if not self.kg_memory:
            return {"question": question, "answer": None, "reason": "知识库未配置"}
        kg_query = KGQuery(working_dir=self.kg_memory.working_dir, kg_memory=self.kg_memory)
        # KGQuery.query 内部对 KGMemory 专属 loop 调 run_until_complete，
        # 在运行中的 lifecycle loop 内直调会抛嵌套 loop RuntimeError，须进工作线程
        answer = await asyncio.to_thread(kg_query.query, question)
        return {"question": question, "answer": answer}
