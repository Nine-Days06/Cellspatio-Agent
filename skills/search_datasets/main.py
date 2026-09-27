"""GEO 数据集检索技能：真实委派 GEOFetcher.search（工作线程内执行）。"""
import asyncio
from dataclasses import asdict

from src.config import NCBI_API_KEY, NCBI_EMAIL
from src.data.fetchers.geo_fetcher import GEOFetcher
from src.skills.base import SkillBase, SkillContext
from src.skills.manifest import SkillMetadata


class SearchDatasetsSkill(SkillBase):
    metadata = SkillMetadata(
        name="search_datasets",
        version="1.0.0",
        description="检索 NCBI GEO 公共数据集候选",
        author="CellSpatio",
        tags=["fetch", "geo"],
    )

    async def execute(self, context: SkillContext) -> dict:
        query = context.params.get("user_input", "").strip()
        # 同步 httpx 阻塞调用，须进工作线程避免卡死 event loop
        results = await asyncio.to_thread(
            GEOFetcher(api_key=NCBI_API_KEY, email=NCBI_EMAIL).search, query, 10
        )
        return {
            "query": query,
            "results": [asdict(meta) for meta in results],
            "count": len(results),
        }
