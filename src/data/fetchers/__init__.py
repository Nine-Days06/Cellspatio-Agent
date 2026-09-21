"""公共数据库 fetcher 集合"""

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher
from src.data.fetchers.geo_fetcher import GEOFetcher
from src.data.fetchers.kegg_fetcher import KEGGFetcher
from src.data.fetchers.uniprot_fetcher import UniProtFetcher

__all__ = [
    "AssetInfo", "AssetMeta", "BaseFetcher",
    "GEOFetcher", "KEGGFetcher", "UniProtFetcher",
]