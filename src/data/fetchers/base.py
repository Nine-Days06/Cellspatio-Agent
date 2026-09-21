"""统一数据获取接口与数据模型"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from src.data.storage import FetcherStorage


@dataclass
class AssetMeta:
    """检索候选的元数据"""
    asset_id: str
    title: str
    source: str
    asset_type: str  # "analysis" | "knowledge"


@dataclass
class AssetInfo:
    """资产确认详情"""
    asset_id: str
    title: str
    source: str
    asset_type: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseFetcher:
    """统一数据获取接口：search → confirm → download/ingest_text

    - search：检索候选列表
    - confirm：资产详情供用户确认
    - download：分析流，落盘 data/raw
    - ingest_text：知识流，返回可入库文本
    """

    source: str = "base"
    asset_type: str = "knowledge"

    def __init__(
        self,
        storage: "FetcherStorage | None" = None,
        client: httpx.Client | None = None,
        **kwargs: Any,
    ):
        self._storage = storage
        self._client = client

    def _get_client(self) -> httpx.Client:
        """返回注入的 client，未注入则延迟创建（测试通过依赖注入 mock）"""
        if self._client is None:
            self._client = httpx.Client(timeout=30)
        return self._client

    def search(self, query: str, max_results: int = 20) -> list[AssetMeta]:
        raise NotImplementedError

    def confirm(self, asset_id: str) -> AssetInfo:
        raise NotImplementedError

    def download(self, asset_id: str) -> Path:
        """分析流：返回本地文件路径"""
        raise NotImplementedError

    def ingest_text(self, asset_id: str) -> str:
        """知识流：返回可入库文本"""
        raise NotImplementedError

    def _save_bytes(self, asset_id: str, data: bytes, filename: str | None = None) -> Path:
        """将内容落盘到 storage，未配置 storage 时抛错"""
        if self._storage is None:
            raise ValueError("storage 未配置，无法落盘")
        return self._storage.save(self.source, asset_id, data, filename=filename)