"""fetcher 注册表"""
from src.data.fetchers.base import BaseFetcher


class FetcherRegistry:
    """按 source 注册与分发 fetcher"""

    def __init__(self):
        self._fetchers: dict[str, BaseFetcher] = {}

    def register(self, fetcher: BaseFetcher):
        """注册 fetcher，以 fetcher.source 为键"""
        self._fetchers[fetcher.source] = fetcher

    def get(self, source: str) -> BaseFetcher:
        """按 source 取 fetcher，未注册抛 KeyError"""
        if source not in self._fetchers:
            raise KeyError(f"未注册的数据源: {source}，可选: {self.sources()}")
        return self._fetchers[source]

    def sources(self) -> list[str]:
        """已注册的数据源名称列表"""
        return list(self._fetchers.keys())

    def has(self, source: str) -> bool:
        """数据源是否已注册"""
        return source in self._fetchers

    @staticmethod
    def build_default(storage=None) -> "FetcherRegistry":
        """注册 GEO / KEGG / UniProt 三个试点 fetcher"""
        registry = FetcherRegistry()
        from src.config import NCBI_API_KEY, NCBI_EMAIL
        from src.data.fetchers.geo_fetcher import GEOFetcher
        from src.data.fetchers.kegg_fetcher import KEGGFetcher
        from src.data.fetchers.uniprot_fetcher import UniProtFetcher

        registry.register(KEGGFetcher(storage=storage))
        registry.register(UniProtFetcher(storage=storage))
        registry.register(GEOFetcher(storage=storage, api_key=NCBI_API_KEY, email=NCBI_EMAIL))
        return registry