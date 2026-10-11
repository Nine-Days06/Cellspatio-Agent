"""KEGG 通路数据获取"""
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher


class KEGGFetcher(BaseFetcher):
    """KEGG REST API（rest.kegg.jp）通路数据获取

    频率限制：3 请求/秒（IP 级，官方要求）
    """

    source = "kegg"
    asset_type = "knowledge"
    base_url = "https://rest.kegg.jp"
    _RATE_LIMIT = 3.0  # 请求/秒
    _last_request_time = 0.0

    def __init__(self, storage=None, client: httpx.Client | None = None):
        super().__init__(storage=storage, client=client)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path}"

    def _get(self, path: str) -> str:
        # 频率限制：3 请求/秒
        min_interval = 1.0 / self._RATE_LIMIT
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

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
        """通路条目原文转为可入库文本（首行 header + 来源 URL）"""
        raw = self._get(f"get/{asset_id}")
        url = f"https://www.kegg.jp/pathway/{asset_id}"
        return f"# KEGG 通路: {asset_id}\n来源：{url}\n\n{raw}\n"

    def ingest_kgml_text(self, asset_id: str) -> str:
        """KGML 通路结构 → 可入库文本（首行 header + 来源 URL）

        KGML 是通路拓扑：entry（gene/group/map/compound）+ relation + reaction。
        relation/reaction 的 entry1/entry2 是 entry id 引用，需先建 id→显示名映射。
        """
        xml_text = self._get(f"get/{asset_id}/kgml")
        url = f"https://www.kegg.jp/pathway/{asset_id}"
        return f"# KEGG 通路结构: {asset_id}\n来源：{url}\n\n{self._kgml_text(xml_text)}\n"

    @staticmethod
    def _kgml_text(xml_text: str) -> str:
        """KGML XML → 结构文本：名称/基因/化合物/关系/反应

        基因显示名取 graphics.name（逗号分隔多别名）；基因统计按 name 字段
        的基因个数（空格分隔）；relation 渲染 "A -| B (subtype)" 按 type 分
        GErel/PPrel 等符号映射；reaction 渲染 "底物 → 产物"。
        """
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
        title = root.get("title") or ""
        entries = root.findall("entry")

        id_to_name: dict[str, str] = {}
        gene_count = 0
        genes: list[str] = []
        compounds: list[str] = []
        for e in entries:
            eid = e.get("id", "")
            etype = e.get("type", "")
            name = e.get("name", "")
            g = e.find("graphics")
            # 显示名优先 graphics@name，缺失时回退 entry@name
            disp = (g.get("name") if g is not None else "") or name
            if etype == "gene":
                # name 形如 "hsa:1029" 或 "hsa:4171 hsa:4172"（多基因空格分隔）
                gene_count += len(name.split())
                if disp and disp not in genes:
                    genes.append(disp)
                id_to_name[eid] = disp
            elif etype == "compound":
                if disp and disp not in compounds:
                    compounds.append(disp)
                id_to_name[eid] = disp
            else:
                id_to_name[eid] = disp or name

        lines = [f"名称：{title}"]
        lines.append(f"基因（{gene_count}）: {', '.join(genes)}")
        lines.append(f"化合物（{len(compounds)}）: {', '.join(compounds)}")

        rel_symbols = {
            "GErel": "-|", "PPrel": "→", "ECrel": "->",
            "PCrel": "=|", "MApl": "--|", "maplink": "--|",
        }
        relations = root.findall("relation")
        if relations:
            lines.append(f"关系（{len(relations)}）:")
            for rel in relations:
                a = id_to_name.get(rel.get("entry1", ""), rel.get("entry1", ""))
                b = id_to_name.get(rel.get("entry2", ""), rel.get("entry2", ""))
                sym = rel_symbols.get(rel.get("type", ""), "-|")
                sub = rel.find("subtype")
                stype = sub.get("name", "") if sub is not None else ""
                suffix = f" ({stype})" if stype else ""
                lines.append(f"- {a} {sym} {b}{suffix}")
        else:
            lines.append("关系（0）: 无")

        reactions = root.findall("reaction")
        if reactions:
            lines.append(f"反应（{len(reactions)}）:")
            for rx in reactions:
                subs = [id_to_name.get(s.get("id", ""), s.get("id", "")) for s in rx.findall("substrate")]
                prods = [id_to_name.get(p.get("id", ""), p.get("id", "")) for p in rx.findall("product")]
                lines.append(f"- {' + '.join(subs)} → {' + '.join(prods)}")
        else:
            lines.append("反应（0）: 无")
        return "\n".join(lines)

    @staticmethod
    def _parse_field(text: str, field: str) -> str:
        """KEGG 条目为 '字段名  值' 格式，解析首行值"""
        prefix = f"{field}  "
        for line in text.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""