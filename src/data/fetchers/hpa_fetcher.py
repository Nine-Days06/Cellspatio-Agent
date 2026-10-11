"""HPA（Human Protein Atlas）蛋白数据获取

search_download API：GET /api/search_download.php?search=<基因>&format=json&columns=...
实测有效列代码仅 g/gs/eg/up/pc/pe/scml（subloc/rna_ts 等被静默丢弃）；
检索为模糊匹配（search=CD4 返回 155 条），必须按 Gene 列精确过滤。
频率：保守 ~1 req/s（连续请求间 sleep 1.1s，实测 12 基因连打无封禁）。
"""
import time
from pathlib import Path

import httpx

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher

HPA_SEARCH_URL = "https://www.proteinatlas.org/api/search_download.php"
HPA_COLUMNS = "g,gs,eg,up,pc,pe,scml"


class HPAFetcher(BaseFetcher):
    """HPA 蛋白数据获取（按基因符号精确匹配）"""

    source = "hpa"
    asset_type = "knowledge"

    def __init__(self, storage=None, client: httpx.Client | None = None):
        super().__init__(storage=storage, client=client)
        self._last_request_time = 0.0

    def _search_rows(self, gene: str) -> list[dict]:
        """请求 search_download 并返回 JSON 数组；精确匹配 Gene 列的条目"""
        min_interval = 1.0 / 1.0  # ~1 req/s 保守限速
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

        resp = self._get_client().get(
            HPA_SEARCH_URL,
            params={"search": gene, "format": "json", "columns": HPA_COLUMNS, "compress": "no"},
        )
        resp.raise_for_status()
        rows = resp.json()
        exact = [r for r in rows if r.get("Gene") == gene]
        if not exact:
            raise ValueError(f"HPA 无 {gene} 的精确匹配（模糊结果 {len(rows)} 条）")
        return exact

    def _to_meta(self, row: dict) -> AssetMeta:
        gene = row.get("Gene", "")
        return AssetMeta(asset_id=gene, title=gene, source=self.source, asset_type=self.asset_type)

    def search(self, query: str, max_results: int = 20) -> list[AssetMeta]:
        """检索基因并精确过滤；可能无精确匹配 → 返回空列表"""
        try:
            rows = self._search_rows(query)
        except ValueError:
            return []
        return [self._to_meta(r) for r in rows[:max_results]]

    def confirm(self, asset_id: str) -> AssetInfo:
        rows = self._search_rows(asset_id)
        row = rows[0]
        classes = " ".join(row.get("Protein class") or [])
        return AssetInfo(
            asset_id=asset_id,
            title=asset_id,
            source=self.source, asset_type=self.asset_type,
            description=classes,
            metadata={
                "ensembl": row.get("Ensembl", ""),
                "uniprot": row.get("Uniprot") or [],
            },
        )

    def download(self, asset_id: str) -> Path:
        raise NotImplementedError(f"{self.source} 是知识流数据源，无文件下载")

    def ingest_text(self, asset_id: str) -> str:
        """蛋白条目 → 可入库文本（首行 header + 来源 URL）"""
        row = self._search_rows(asset_id)[0]
        ensembl = row.get("Ensembl", "")
        url = f"https://www.proteinatlas.org/{ensembl}" if ensembl else "https://www.proteinatlas.org/"
        lines = [f"# HPA 蛋白: {asset_id}", f"来源：{url}"]
        synonyms = row.get("Gene synonym") or []
        if synonyms:
            lines.append(f"别名：{', '.join(synonyms)}")
        if ensembl:
            lines.append(f"Ensembl：{ensembl}")
        uniprot = row.get("Uniprot") or []
        if uniprot:
            lines.append(f"UniProt：{', '.join(uniprot)}")
        classes = row.get("Protein class") or []
        lines.append(f"蛋白类别：{'; '.join(classes)}" if classes else "蛋白类别：无")
        evidence = row.get("Evidence", "")
        if evidence:
            lines.append(f"证据：{evidence}")
        locations = row.get("Subcellular main location") or []
        if locations:
            lines.append(f"亚细胞定位：{', '.join(locations)}")
        return "\n".join(lines) + "\n"
