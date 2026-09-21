"""KEGG 通路数据获取"""
from pathlib import Path
from urllib.parse import quote

import httpx

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher


class KEGGFetcher(BaseFetcher):
    """KEGG REST API（rest.kegg.jp）通路数据获取"""

    source = "kegg"
    asset_type = "knowledge"
    base_url = "https://rest.kegg.jp"

    def __init__(self, storage=None, client: httpx.Client | None = None, api_key: str = ""):
        super().__init__(storage=storage, client=client)
        self.api_key = api_key

    def _url(self, path: str) -> str:
        url = f"{self.base_url}/{path}"
        if self.api_key:
            url += f"?key={self.api_key}"
        return url

    def _get(self, path: str) -> str:
        resp = self._get_client().get(self._url(path))
        resp.raise_for_status()
        return resp.text

    def search(self, query: str, max_results: int = 20) -> list[AssetMeta]:
        """find/pathway 检索通路，解析 'path:id<TAB>title' 行"""
        text = self._get(f"find/pathway/{quote(query)}")
        metas = []
        for line in text.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            pathway_id = parts[0].split(":", 1)[-1]
            metas.append(AssetMeta(
                asset_id=pathway_id, title=parts[1],
                source=self.source, asset_type=self.asset_type,
            ))
            if len(metas) >= max_results:
                break
        return metas

    def confirm(self, asset_id: str) -> AssetInfo:
        """get 通路条目，解析 NAME 与 DESCRIPTION"""
        text = self._get(f"get/{asset_id}")
        return AssetInfo(
            asset_id=asset_id,
            title=self._parse_field(text, "NAME") or asset_id,
            source=self.source,
            asset_type=self.asset_type,
            description=self._parse_field(text, "DESCRIPTION"),
            metadata={"raw_length": len(text)},
        )

    def download(self, asset_id: str) -> Path:
        """KEGG 为知识流，不支持下载"""
        raise NotImplementedError(f"{self.source} 是知识流数据源，无文件下载")

    def ingest_text(self, asset_id: str) -> str:
        """通路条目原文转为可入库文本"""
        raw = self._get(f"get/{asset_id}")
        return f"# KEGG 通路: {asset_id}\n\n{raw}\n"

    @staticmethod
    def _parse_field(text: str, field: str) -> str:
        """KEGG 条目为 '字段名  值' 格式，解析首行值"""
        prefix = f"{field}  "
        for line in text.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""