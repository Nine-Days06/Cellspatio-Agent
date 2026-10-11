"""Reactome ContentService 通路数据获取

ContentService 官方 REST（https://reactome.org/ContentService/）：
- query：GET /data/query/<stId>（通路详情 JSON）
- containedEvents：GET /data/pathway/<stId>/containedEvents（子事件列表，可能上百条）
旧域名 reactome.org 实测可用；新 api.reactome.org 本机连接超时，不采用。
"""
import time
from pathlib import Path

import httpx

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher


class ReactomeFetcher(BaseFetcher):
    """Reactome ContentService 通路数据获取"""

    source = "reactome"
    asset_type = "knowledge"
    base_url = "https://reactome.org/ContentService"

    def __init__(self, storage=None, client: httpx.Client | None = None):
        super().__init__(storage=storage, client=client)

    def _get_json(self, path: str) -> list | dict:
        """GET JSON，遇 SSL 超时重试 2 次（实测 /search/query 偶发超时）"""
        last: Exception | None = None
        for _ in range(3):
            try:
                resp = self._get_client().get(f"{self.base_url}{path}")
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, OSError) as exc:
                last = exc
                time.sleep(1.0)
        raise RuntimeError(f"Reactome 请求失败: {path}: {last}") from last

    def search(self, query: str, max_results: int = 20) -> list[AssetMeta]:
        """顶层通路列表（/data/pathways/top/<species>），query 为物种名"""
        data = self._get_json(f"/data/pathways/top/{query}")
        metas = []
        for item in data[:max_results]:
            metas.append(AssetMeta(
                asset_id=item.get("stId", ""),
                title=item.get("displayName", ""),
                source=self.source, asset_type=self.asset_type,
            ))
        return metas

    def confirm(self, asset_id: str) -> AssetInfo:
        """query 通路详情，提取标题与摘要"""
        data = self._get_json(f"/data/query/{asset_id}")
        summation = data.get("summation") or []
        description = (summation[0].get("text", "") if summation else "") or ""
        return AssetInfo(
            asset_id=asset_id,
            title=data.get("displayName", asset_id),
            source=self.source, asset_type=self.asset_type,
            description=description,
            metadata={"is_in_disease": data.get("isInDisease", False)},
        )

    def download(self, asset_id: str) -> Path:
        raise NotImplementedError(f"{self.source} 是知识流数据源，无文件下载")

    def ingest_text(self, asset_id: str) -> str:
        """通路详情 + 子事件（封顶 50 条）→ 可入库文本"""
        data = self._get_json(f"/data/query/{asset_id}")
        url = f"https://reactome.org/ContentService/data/query/{asset_id}"
        lines = [f"# Reactome 通路: {asset_id}", f"来源：{url}"]
        lines.append(f"名称：{data.get('displayName', '')}")
        lines.append(f"物种：{data.get('speciesName', '')}")
        go = data.get("goBiologicalProcess")
        if go:
            lines.append(f"GO 生物过程：{go.get('displayName', '')} (GO:{go.get('accession', '')})")
        if data.get("isInDisease"):
            lines.append("疾病相关：是")
        summation = data.get("summation") or []
        summary = (summation[0].get("text", "") if summation else "") or ""
        # 摘要含 HTML 标签（实测有 <b> 等），剥掉后渲染
        import re
        summary = re.sub(r"<[^>]+>", "", summary)
        lines.append(f"摘要：{summary}" if summary else "摘要：无")
        try:
            events = self._get_json(f"/data/pathway/{asset_id}/containedEvents")
        except RuntimeError:
            events = []
        shown = events[:50]
        names = [f"{e.get('stId', '')} {e.get('displayName', '')}" for e in shown]
        cap = "（50）" if len(events) > 50 else f"（{len(events)}）"
        line = f"子事件{cap}: " + "; ".join(names)
        if len(events) > 50:
            line += f" …等 {len(events)} 条"
        lines.append(line)
        return "\n".join(lines) + "\n"
