"""GEO 转录组数据获取（NCBI E-utilities + GEO FTP）"""
from pathlib import Path
from typing import Any

import httpx

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher


class GEOFetcher(BaseFetcher):
    """GEO 数据集获取（分析流）

    - search：E-utilities esearch（db=gds）+ esummary 转 GSE accession
    - download：GEO FTP 拉取 Series Matrix 文件（.txt.gz）
    - ingest_text：GSE 元数据转文本（供「找数据集」类知识问答）
    """

    source = "geo"
    asset_type = "analysis"
    EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    GEO_FTP_URL = "https://ftp.ncbi.nlm.nih.gov/geo/series"

    def __init__(
        self,
        storage=None,
        client: httpx.Client | None = None,
        api_key: str = "",
        email: str = "",
    ):
        super().__init__(storage=storage, client=client)
        self.api_key = api_key
        self.email = email

    def _eutils_params(self, **extra: Any) -> dict[str, Any]:
        """拼接 E-utilities 公共参数（api_key/email/retmode json）"""
        params: dict[str, Any] = {"retmode": "json", **extra}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["email"] = self.email
        return params

    def search(self, query: str, max_results: int = 20) -> list[AssetMeta]:
        """esearch（db=gds）→ esummary → GSE accession 候选"""
        client = self._get_client()
        params = self._eutils_params(db="gds", term=query, retmax=max_results)
        resp = client.get(f"{self.EUTILS_URL}/esearch.fcgi", params=params)
        resp.raise_for_status()
        idlist = resp.json().get("esearchresult", {}).get("idlist", [])
        if not idlist:
            return []
        return self._summaries_by_ids(idlist)

    def confirm(self, asset_id: str) -> AssetInfo:
        """按 GSE accession（如 GSE123456[ACCN]）查询并返回详情"""
        client = self._get_client()
        params = self._eutils_params(db="gds", term=f"{asset_id}[ACCN]", retmax=1)
        resp = client.get(f"{self.EUTILS_URL}/esearch.fcgi", params=params)
        resp.raise_for_status()
        idlist = resp.json().get("esearchresult", {}).get("idlist", [])
        if not idlist:
            raise ValueError(f"GEO 未找到数据集: {asset_id}")
        entry = self._fetch_summary(idlist[0])
        return self._to_info(entry)

    def download(self, asset_id: str) -> Path:
        """下载 Series Matrix 文件到 data/raw/geo/<GSE>/，已缓存则直接返回"""
        if self._storage is None:
            raise ValueError("storage 未配置，无法落盘")
        filename = f"{asset_id}_series_matrix.txt.gz"
        if self._storage.exists(self.source, asset_id, filename=filename):
            return self._storage.get_path(self.source, asset_id, filename=filename)

        resp = self._get_client().get(self._series_matrix_url(asset_id))
        if resp.status_code == 404:
            raise ValueError(f"GEO Series Matrix 不存在: {asset_id}")
        resp.raise_for_status()
        return self._storage.save(self.source, asset_id, resp.content, filename=filename)

    def ingest_text(self, asset_id: str) -> str:
        """GSE 元数据转文本"""
        info = self.confirm(asset_id)
        m = info.metadata
        lines = [
            f"# GEO 数据集: {info.asset_id}",
            f"来源：https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={info.asset_id}",
            f"标题：{info.title}",
            f"摘要：{info.description}",
            f"类型：{m.get('gdsType', '')}",
            f"物种：{m.get('taxon', '')}",
            f"样本数：{m.get('n_samples', '')}",
            f"发布日期：{m.get('PDAT', '')}",
        ]
        return "\n".join(lines)

    def _summaries_by_ids(self, idlist: list[str]) -> list[AssetMeta]:
        """批量 esummary 并转为候选"""
        metas = []
        for uid in idlist:
            entry = self._fetch_summary(uid)
            if not entry:
                continue
            accession = entry.get("accession", "")
            if not accession.upper().startswith("GSE"):
                continue
            metas.append(AssetMeta(
                asset_id=accession, title=entry.get("title", accession),
                source=self.source, asset_type=self.asset_type,
            ))
        return metas

    def _fetch_summary(self, uid: str) -> dict[str, Any]:
        """单个 GDS id 的 esummary"""
        client = self._get_client()
        params = self._eutils_params(db="gds", id=uid)
        resp = client.get(f"{self.EUTILS_URL}/esummary.fcgi", params=params)
        resp.raise_for_status()
        result = resp.json().get("result", {})
        return result.get(str(uid), {})

    def _to_info(self, entry: dict[str, Any]) -> AssetInfo:
        """esummary 条目转 AssetInfo"""
        return AssetInfo(
            asset_id=entry.get("accession", ""),
            title=entry.get("title", ""),
            source=self.source,
            asset_type=self.asset_type,
            description=entry.get("summary", ""),
            metadata={
                k: entry.get(k)
                for k in ("gdsType", "taxon", "n_samples", "PDAT")
            },
        )

    @staticmethod
    def _series_range_dir(accession: str) -> str:
        """NCBI FTP 区间目录：accession 数字末 3 位换成 nnn

        GSE48351 -> GSE48nnn, GSE123456 -> GSE123nnn, GSE1234 -> GSE1nnn, GSE1 -> GSEnnn
        """
        digits = accession[3:] if accession[:3].isalpha() else accession
        if len(digits) <= 3:
            return f"{accession[:3] if accession[:3].isalpha() else 'GSE'}nnn"
        return f"{accession[:-3]}nnn"

    def _series_matrix_url(self, accession: str) -> str:
        """构造 GEO Series Matrix 下载地址"""
        sub = self._series_range_dir(accession)
        return (
            f"{self.GEO_FTP_URL}/{sub}/{accession}/matrix/"
            f"{accession}_series_matrix.txt.gz"
        )