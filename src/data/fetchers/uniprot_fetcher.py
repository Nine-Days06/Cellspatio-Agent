"""UniProt 蛋白数据获取"""
from pathlib import Path

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher


class UniProtFetcher(BaseFetcher):
    """UniProt REST API（rest.uniprot.org）蛋白数据获取"""

    source = "uniprot"
    asset_type = "knowledge"
    base_url = "https://rest.uniprot.org"

    def search(self, query: str, max_results: int = 20) -> list[AssetMeta]:
        """uniprotkb/search 检索蛋白，取 accession / protein name"""
        params = {
            "query": query, "format": "json",
            "fields": "accession,protein_name,gene_names",
            "size": max_results,
        }
        resp = self._get_client().get(f"{self.base_url}/uniprotkb/search", params=params)
        resp.raise_for_status()
        metas = []
        for item in resp.json().get("results", []):
            accession = item.get("primaryAccession")
            if not accession:
                continue
            metas.append(AssetMeta(
                asset_id=accession, title=self._protein_name(item),
                source=self.source, asset_type=self.asset_type,
            ))
        return metas

    def confirm(self, asset_id: str) -> AssetInfo:
        """uniprotkb/<id>.json 获取蛋白详情"""
        resp = self._get_client().get(f"{self.base_url}/uniprotkb/{asset_id}.json")
        if resp.status_code == 404:
            raise ValueError(f"UniProt 未找到蛋白: {asset_id}")
        resp.raise_for_status()
        item = resp.json()
        return AssetInfo(
            asset_id=asset_id,
            title=self._protein_name(item),
            source=self.source,
            asset_type=self.asset_type,
            description=self._comments_text(item),
            metadata={"gene": self._gene_name(item)},
        )

    def download(self, asset_id: str) -> Path:
        """UniProt 为知识流，不支持下载"""
        raise NotImplementedError(f"{self.source} 是知识流数据源，无文件下载")

    def ingest_text(self, asset_id: str) -> str:
        """蛋白条目转为可入库 Markdown"""
        resp = self._get_client().get(f"{self.base_url}/uniprotkb/{asset_id}.json")
        resp.raise_for_status()
        item = resp.json()
        lines = [
            f"# UniProt 蛋白: {asset_id}",
            # URL 末段必须是 accession：LightRAG 只存 basename，`/entry` 会撞车
            f"来源：https://www.uniprot.org/uniprotkb/{asset_id}",
            f"蛋白名：{self._protein_name(item)}",
            f"基因：{self._gene_name(item)}",
        ]
        for comment in item.get("comments", []):
            for text in comment.get("text", []):
                value = text.get("value") if isinstance(text, dict) else text
                if isinstance(value, str):
                    lines.append(f"- {comment.get('commentType', '')}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _protein_name(item: dict) -> str:
        """提取蛋白推荐名或首个常用名"""
        description = item.get("proteinDescription", {})
        name = description.get("recommendedName", {}).get("fullName", {}).get("value")
        if not name:
            for sub in description.get("subNames", []):
                name = sub.get("fullName", {}).get("value")
                if name:
                    break
        return name or item.get("primaryAccession", "未知蛋白")

    @staticmethod
    def _gene_name(item: dict) -> str:
        """提取首个基因名"""
        genes = item.get("genes", [])
        if genes:
            return genes[0].get("geneName", {}).get("value", "")
        return ""

    @staticmethod
    def _comments_text(item: dict, limit: int = 3) -> str:
        """汇总前 N 条 comment 文本"""
        texts = []
        for comment in item.get("comments", []):
            for text in comment.get("text", []):
                value = text.get("value") if isinstance(text, dict) else text
                if isinstance(value, str):
                    texts.append(value)
            if len(texts) >= limit:
                break
        return "; ".join(texts)