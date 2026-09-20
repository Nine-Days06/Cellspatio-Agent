# 数据获取层实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现统一公共数据库获取层（GEO / KEGG / UniProt 三个 fetcher），打通检索、确认、下载、转文本四段接口，落盘与缓存生效。

**架构：** 在 `src/data/` 下新增 `fetchers/`、`storage.py`、`registry.py`。`BaseFetcher` 定义统一接口，三个子类分别封装 E-utilities（GEO）、KEGG REST、UniProt REST。`FetcherStorage` 管理 `data/raw/<source>/<asset_id>/` 落盘与缓存；`FetcherRegistry` 按 source 分发并默认注册三个 fetcher。

**技术栈：** httpx（同步 Client，支持 MockTransport 测试）、路径库、pytest。

**前置：** 本计划对应规格 `docs/superpowers/specs/2026-09-20-data-fetcher-and-lightrag-config-design.md` 第 4 章与试点里程碑 M1。执行环境为 Windows/PowerShell，Python 3.10+。

---

### 任务 1：FetcherStorage——资产落盘与缓存

**文件：**
- 创建：`src/data/storage.py`
- 测试：`tests/unit/test_storage.py`

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_storage.py`：

```python
from src.data.storage import FetcherStorage


def test_save_and_exists():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    path = storage.save("kegg", "hsa00010", b"raw content")
    assert path.name == "hsa00010.raw"
    assert storage.exists("kegg", "hsa00010")
    assert storage.get_path("kegg", "hsa00010") == path


def test_save_skips_existing_file():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    path1 = storage.save("kegg", "hsa00010", b"v1")
    path2 = storage.save("kegg", "hsa00010", b"v2-changed")
    assert path1 == path2
    assert path1.read_bytes() == b"v1"


def test_save_with_filename():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    path = storage.save("geo", "GSE123", b"matrix", filename="GSE123_series_matrix.txt.gz")
    assert path.name == "GSE123_series_matrix.txt.gz"


def test_meta_roundtrip():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    storage.save_meta("geo", "GSE123", {"title": "Demo"})
    assert storage.load_meta("geo", "GSE123") == {"title": "Demo"}
    assert storage.load_meta("geo", "NOPE") is None


def test_list_assets():
    storage = FetcherStorage(base_dir="test_storage/raw", meta_dir="test_storage/meta")
    storage.save("kegg", "hsa00010", b"x")
    storage.save("uniprot", "P04637", b"y")
    assets = storage.list_assets()
    assert any("hsa00010" in a for a in assets)
    assert any("P04637" in a for a in assets)
    kegg_only = storage.list_assets("kegg")
    assert all("kegg" in a for a in kegg_only)
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_storage.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.data.storage'`

- [ ] **步骤 3：实现 FetcherStorage**

创建 `src/data/storage.py`：

```python
"""数据资产落盘与缓存"""
import json
from pathlib import Path
from typing import Any


class FetcherStorage:
    """管理公共数据资产的本地落盘与缓存"""

    def __init__(self, base_dir: str | Path = "data/raw", meta_dir: str | Path | None = None):
        self.base_dir = Path(base_dir)
        self.meta_dir = Path(meta_dir) if meta_dir else self.base_dir / ".meta"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    def _asset_dir(self, source: str, asset_id: str) -> Path:
        """资产目录：data/raw/<source>/<asset_id>/"""
        return self.base_dir / source / asset_id

    def save(self, source: str, asset_id: str, data: bytes, filename: str | None = None) -> Path:
        """保存字节内容，已存在则直接返回不覆盖"""
        asset_dir = self._asset_dir(source, asset_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        path = asset_dir / (filename or f"{asset_id}.raw")
        if path.exists():
            return path
        path.write_bytes(data)
        return path

    def exists(self, source: str, asset_id: str, filename: str | None = None) -> bool:
        """资产是否已存在"""
        return self.get_path(source, asset_id, filename=filename).exists()

    def get_path(self, source: str, asset_id: str, filename: str | None = None) -> Path:
        """计算资产路径（不保证存在）"""
        return self._asset_dir(source, asset_id) / (filename or f"{asset_id}.raw")

    def save_meta(self, source: str, asset_id: str, info: dict[str, Any]) -> Path:
        """保存资产元数据 JSON"""
        meta_file = self.meta_dir / f"{source}_{asset_id}.json"
        meta_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta_file

    def load_meta(self, source: str, asset_id: str) -> dict[str, Any] | None:
        """读取资产元数据，不存在返回 None"""
        meta_file = self.meta_dir / f"{source}_{asset_id}.json"
        if not meta_file.exists():
            return None
        return json.loads(meta_file.read_text(encoding="utf-8"))

    def list_assets(self, source: str | None = None) -> list[str]:
        """列出全部或某来源下的资产文件（相对路径）"""
        base = self.base_dir if source is None else self.base_dir / source
        if not base.exists():
            return []

        def walk(dir_: Path, prefix: str = "") -> list[str]:
            assets = []
            for sub in dir_.iterdir():
                if sub.is_dir():
                    assets.extend(walk(sub, f"{prefix}{sub.name}/"))
                else:
                    assets.append(f"{prefix}{sub.name}")
            return assets

        return walk(base)
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_storage.py -v`
预期：PASS（5 tests passed）

- [ ] **步骤 5：Commit**

```bash
git add src/data/storage.py tests/unit/test_storage.py
git commit -m "feat: 数据资产落盘与缓存 FetcherStorage"
```

---

### 任务 2：数据模型与 BaseFetcher 抽象接口

**文件：**
- 创建：`src/data/fetchers/base.py`
- 测试：`tests/unit/test_base_fetcher.py`

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_base_fetcher.py`：

```python
import httpx
import pytest

from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher
from src.data.storage import FetcherStorage


def test_asset_meta_fields():
    meta = AssetMeta(asset_id="hsa00010", title="Glycolysis", source="kegg", asset_type="knowledge")
    assert meta.asset_id == "hsa00010"
    assert meta.asset_type == "knowledge"


def test_asset_info_defaults():
    info = AssetInfo(asset_id="P04637", title="p53", source="uniprot", asset_type="knowledge")
    assert info.description == ""
    assert info.metadata == {}


class ConcreteFetcher(BaseFetcher):
    source = "demo"
    asset_type = "knowledge"

    def search(self, query, max_results=20):
        return []

    def confirm(self, asset_id):
        return AssetInfo(asset_id=asset_id, title="demo", source="demo", asset_type="knowledge")


def test_base_abstract_methods_raise():
    fetcher = ConcreteFetcher()
    with pytest.raises(NotImplementedError):
        fetcher.download("x")
    with pytest.raises(NotImplementedError):
        fetcher.ingest_text("x")


def test_base_client_injection_and_lazy_create():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    injected = httpx.Client(transport=transport)
    fetcher = ConcreteFetcher(client=injected)
    assert fetcher._get_client() is injected

    lazy = ConcreteFetcher()
    assert isinstance(lazy._get_client(), httpx.Client)
    assert lazy._get_client() is lazy._get_client()
    assert lazy._client is not None


def test_base_save_bytes_without_storage_raises():
    fetcher = ConcreteFetcher()
    with pytest.raises(ValueError):
        fetcher._save_bytes("demo", b"data")


def test_base_save_bytes_with_storage(tmp_path):
    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    fetcher = ConcreteFetcher(storage=storage)
    path = fetcher._save_bytes("demo", b"hello", filename="a.txt")
    assert path.name == "a.txt"
    assert path.read_bytes() == b"hello"
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_base_fetcher.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.data.fetchers.base'`

- [ ] **步骤 3：实现数据模型与 BaseFetcher**

创建 `src/data/fetchers/base.py`：

```python
"""统一数据获取接口与数据模型"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


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
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_base_fetcher.py -v`
预期：PASS（5 tests passed）

- [ ] **步骤 5：Commit**

```bash
git add src/data/fetchers/base.py tests/unit/test_base_fetcher.py
git commit -m "feat: 数据获取统一接口 BaseFetcher 与数据模型"
```

---

### 任务 3：FetcherRegistry 注册表

**文件：**
- 创建：`src/data/registry.py`
- 测试：`tests/unit/test_registry.py`

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_registry.py`：

```python
import pytest

from src.data.fetchers.base import BaseFetcher
from src.data.registry import FetcherRegistry


class DemoFetcher(BaseFetcher):
    source = "demo"
    asset_type = "knowledge"


def test_register_and_get():
    registry = FetcherRegistry()
    fetcher = DemoFetcher()
    registry.register(fetcher)
    assert registry.has("demo")
    assert registry.get("demo") is fetcher


def test_get_unregistered_raises():
    registry = FetcherRegistry()
    with pytest.raises(KeyError):
        registry.get("missing")


def test_sources_list():
    registry = FetcherRegistry()
    registry.register(DemoFetcher())
    assert registry.sources() == ["demo"]


def test_build_default_registers_three():
    registry = FetcherRegistry.build_default()
    assert {"geo", "kegg", "uniprot"} <= set(registry.sources())
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_registry.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.data.registry'`

- [ ] **步骤 3：实现 FetcherRegistry**

创建 `src/data/registry.py`：

```python
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
        from src.config import KEGG_API_KEY, NCBI_API_KEY, NCBI_EMAIL
        from src.data.fetchers.geo_fetcher import GEOFetcher
        from src.data.fetchers.kegg_fetcher import KEGGFetcher
        from src.data.fetchers.uniprot_fetcher import UniProtFetcher

        registry.register(KEGGFetcher(storage=storage, api_key=KEGG_API_KEY))
        registry.register(UniProtFetcher(storage=storage))
        registry.register(GEOFetcher(storage=storage, api_key=NCBI_API_KEY, email=NCBI_EMAIL))
        return registry
```

- [ ] **步骤 4：运行测试确认失败（导入 fetcher 尚未创建）**

运行：`python -m pytest tests/unit/test_registry.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.data.fetchers.geo_fetcher'`（任务 3 的 `build_default` 依赖任务 5-7 的 fetcher 模块，注册测试会在后续任务完成后通过）

> 说明：`test_build_default_registers_three` 依赖 GEO/KEGG/UniProt fetcher 类，待任务 5-7 完成后运行 `python -m pytest tests/unit/test_registry.py -v` 验证通过。

- [ ] **步骤 5：Commit**

```bash
git add src/data/registry.py tests/unit/test_registry.py
git commit -m "feat: fetcher 注册表 FetcherRegistry"
```

---

### 任务 4：配置项扩展

**文件：**
- 修改：`src/config.py`
- 修改：`.env.example`

- [ ] **步骤 1：修改配置常量**

在 `src/config.py` 末尾追加外部数据源配置块（放在 `LLM_PROVIDER_CONFIGS` 之前或之后均可）：

```python
# ── 外部数据源配置 ──────────────────────────────────────────
# GEO 检索复用 NCBI E-utilities
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
NCBI_EMAIL   = os.environ.get("NCBI_EMAIL", "")

# KEGG 免费 API key（可选，提升频率限额）
KEGG_API_KEY = os.environ.get("KEGG_API_KEY", "")

# 本地 Ollama（embedding 用，计划 2 消费）
OLLAMA_URL      = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
```

- [ ] **步骤 2：更新 .env.example**

在 `.env.example` 追加：

```
# ── 公共数据库配置（可选） ─────────────────────────────────
# GEO 检索复用上方 NCBI 配置；KEGG 免费 key 提升频率限额
# KEGG_API_KEY=your_kegg_api_key
# OLLAMA_URL=http://localhost:11434
# EMBEDDING_MODEL=bge-m3
```

- [ ] **步骤 3：验证配置可加载**

运行：`python -c "from src.config import KEGG_API_KEY, OLLAMA_URL, EMBEDDING_MODEL, NCBI_API_KEY, NCBI_EMAIL; print(KEGG_API_KEY, OLLAMA_URL, EMBEDDING_MODEL)"`
预期：输出 ` http://localhost:11434 bge-m3`（KEGG_API_KEY 为空字符串）

- [ ] **步骤 4：Commit**

```bash
git add src/config.py .env.example
git commit -m "feat: 新增公共数据库配置项（NCBI/KEGG/Ollama）"
```

---

### 任务 5：KEGGFetcher

**文件：**
- 创建：`src/data/fetchers/kegg_fetcher.py`
- 测试：`tests/unit/test_kegg_fetcher.py`

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_kegg_fetcher.py`：

```python
import httpx

from src.data.fetchers.base import AssetMeta
from src.data.fetchers.kegg_fetcher import KEGGFetcher


def _fetcher_with(handler):
    return KEGGFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_search_parses_find_response():
    def handler(request):
        assert "/find/pathway/glycolysis" in str(request.url)
        return httpx.Response(200, text=(
            "path:hsa00010\tGlycolysis / Gluconeogenesis - Homo sapiens (human)\n"
            "path:ec00010\tGlycolysis / Gluconeogenesis - Escherichia coli K-12\n"
        ))

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("glycolysis")
    assert len(metas) == 2
    assert metas[0].asset_id == "hsa00010"
    assert metas[0].asset_type == "knowledge"
    assert "Glycolysis" in metas[0].title


def test_search_respects_max_results():
    def handler(request):
        return httpx.Response(200, text="path:one001\tA\npath:one002\tB\npath:one003\tC\n")

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("query", max_results=2)
    assert len(metas) == 2


def test_confirm_parses_name_and_description():
    def handler(request):
        assert "/get/hsa00010" in str(request.url)
        return httpx.Response(200, text=(
            "ENTRY       hsa00010                      Pathway\n"
            "NAME        Glycolysis / Gluconeogenesis\n"
            "DESCRIPTION  Glycolysis is the metabolic pathway\n"
            "xxx\n"
            "//\n"
        ))

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("hsa00010")
    assert info.title == "Glycolysis / Gluconeogenesis"
    assert "metabolic pathway" in info.description


def test_ingest_text_wraps_raw_text():
    def handler(request):
        return httpx.Response(200, text="ENTRY       hsa00010\r\n//\r\n")

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("hsa00010")
    assert text.startswith("# KEGG 通路: hsa00010")
    assert "ENTRY" in text


def test_api_key_appended():
    def handler(request):
        assert "key=sekret" in str(request.url)
        return httpx.Response(200, text="path:hsa00010\tT\n")

    fetcher = KEGGFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)), api_key="sekret")
    fetcher.search("glycolysis")


def test_download_raises_not_implemented():
    fetcher = KEGGFetcher()
    try:
        fetcher.download("hsa00010")
    except NotImplementedError:
        assert True
    else:
        raise AssertionError("download 应抛 NotImplementedError")
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_kegg_fetcher.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.data.fetchers.kegg_fetcher'`

- [ ] **步骤 3：实现 KEGGFetcher**

创建 `src/data/fetchers/kegg_fetcher.py`：

```python
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
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_kegg_fetcher.py -v`
预期：PASS（6 tests passed）

- [ ] **步骤 5：Commit**

```bash
git add src/data/fetchers/kegg_fetcher.py tests/unit/test_kegg_fetcher.py
git commit -m "feat: KEGG 通路 fetcher（检索/确认/转文本）"
```

---

### 任务 6：UniProtFetcher

**文件：**
- 创建：`src/data/fetchers/uniprot_fetcher.py`
- 测试：`tests/unit/test_uniprot_fetcher.py`

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_uniprot_fetcher.py`：

```python
import httpx

from src.data.fetchers.uniprot_fetcher import UniProtFetcher


def _fetcher_with(handler):
    return UniProtFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _search_payload():
    return {
        "results": [{
            "primaryAccession": "P04637",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Cellular tumor antigen p53"}}},
            "genes": [{"geneName": {"value": "TP53"}}],
        }]
    }


def test_search_parses_results():
    def handler(request):
        assert "/uniprotkb/search" in str(request.url)
        assert "P04637" not in request.url.path
        return httpx.Response(200, json=_search_payload())

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("TP53")
    assert len(metas) == 1
    assert metas[0].asset_id == "P04637"
    assert metas[0].title == "Cellular tumor antigen p53"
    assert metas[0].asset_type == "knowledge"


def test_confirm_parses_comments_and_gene():
    def handler(request):
        return httpx.Response(200, json={
            "primaryAccession": "P04637",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Cellular tumor antigen p53"}}},
            "genes": [{"geneName": {"value": "TP53"}}],
            "comments": [
                {"commentType": "FUNCTION",
                 "text": [{"value": "Acts as a tumor suppressor."}]},
            ],
        })

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("P04637")
    assert info.title == "Cellular tumor antigen p53"
    assert info.metadata["gene"] == "TP53"
    assert "tumor suppressor" in info.description


def test_confirm_not_found_raises():
    def handler(request):
        return httpx.Response(404)

    fetcher = _fetcher_with(handler)
    try:
        fetcher.confirm("NOPE0")
    except ValueError:
        assert True
    else:
        raise AssertionError("confirm 应抛 ValueError")


def test_ingest_text_builds_markdown():
    def handler(request):
        assert "/uniprotkb/P04637.json" in str(request.url)
        return httpx.Response(200, json={
            "primaryAccession": "P04637",
            "proteinDescription": {"recommendedName": {"fullName": {"value": "p53"}}},
            "genes": [{"geneName": {"value": "TP53"}}],
            "comments": [{"commentType": "FUNCTION", "text": [{"value": "Tumor suppressor."}]}],
        })

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("P04637")
    assert "# UniProt 蛋白: P04637" in text
    assert "基因：TP53" in text
    assert "Tumor suppressor." in text


def test_search_respects_max_results():
    def handler(request):
        return httpx.Response(200, json={"results": [
            {"primaryAccession": f"P0000{i}", "proteinDescription": {"recommendedName": {"fullName": {"value": f"P{i}"}}}}
            for i in range(3)
        ]})

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("query", max_results=3)
    assert len(metas) == 3


def test_download_raises_not_implemented():
    fetcher = UniProtFetcher()
    try:
        fetcher.download("P04637")
    except NotImplementedError:
        assert True
    else:
        raise AssertionError("download 应抛 NotImplementedError")
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_uniprot_fetcher.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.data.fetchers.uniprot_fetcher'`

- [ ] **步骤 3：实现 UniProtFetcher**

创建 `src/data/fetchers/uniprot_fetcher.py`：

```python
"""UniProt 蛋白数据获取"""
from pathlib import Path

import httpx

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
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_uniprot_fetcher.py -v`
预期：PASS（6 tests passed）

- [ ] **步骤 5：Commit**

```bash
git add src/data/fetchers/uniprot_fetcher.py tests/unit/test_uniprot_fetcher.py
git commit -m "feat: UniProt 蛋白 fetcher（检索/确认/转文本）"
```

---

### 任务 7：GEOFetcher（分析流）

**文件：**
- 创建：`src/data/fetchers/geo_fetcher.py`
- 测试：`tests/unit/test_geo_fetcher.py`

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_geo_fetcher.py`：

```python
import httpx

from src.data.fetchers.geo_fetcher import GEOFetcher


def _fetcher_with(handler):
    return GEOFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _esearch_response(ids=None):
    return {"esearchresult": {"idlist": ids or ["200123456"]}}


def _esummary_response():
    return {"result": {
        "uids": ["200123456"],
        "200123456": {
            "accession": "GSE123456",
            "title": "Expression data from diabetes patients",
            "summary": "A summary of the dataset.",
            "gdsType": "Expression profiling by array",
            "taxon": "Homo sapiens",
            "n_samples": 12,
            "PDAT": "2024-01-01",
        },
    }}


def test_search_esearch_then_esummary():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            assert "db=gds" in str(request.url)
            return httpx.Response(200, json=_esearch_response())
        if "esummary.fcgi" in str(request.url):
            return httpx.Response(200, json=_esummary_response())
        return httpx.Response(404)

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("diabetes")
    assert len(metas) == 1
    assert metas[0].asset_id == "GSE123456"
    assert metas[0].asset_type == "analysis"
    assert "diabetes" in metas[0].title


def test_search_empty_idlist():
    def handler(request):
        return httpx.Response(200, json=_esearch_response(ids=[]))

    fetcher = _fetcher_with(handler)
    assert fetcher.search("nothing_matches") == []


def test_confirm_uses_accession_term():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            assert "GSE123456[ACCN]" in str(request.url)
            return httpx.Response(200, json=_esearch_response())
        if "esummary.fcgi" in str(request.url):
            return httpx.Response(200, json=_esummary_response())
        return httpx.Response(404)

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("GSE123456")
    assert info.title == "Expression data from diabetes patients"
    assert info.metadata["taxon"] == "Homo sapiens"


def test_series_matrix_url_rules():
    fetcher = GEOFetcher()
    assert fetcher._series_matrix_url("GSE123456") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE123/GSE123456/matrix/GSE123456_series_matrix.txt.gz"
    )
    assert fetcher._series_matrix_url("GSE12") == (
        "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE012/GSE12/matrix/GSE12_series_matrix.txt.gz"
    )


def test_download_fetches_and_saves(tmp_path):
    def handler(request):
        url = str(request.url)
        assert "ftp.ncbi.nlm.nih.gov/geo/series" in url
        return httpx.Response(200, content=b"matrix-content")

    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    fetcher = GEOFetcher(storage=storage, client=httpx.Client(transport=httpx.MockTransport(handler)))
    path = fetcher.download("GSE123456")
    assert path.name == "GSE123456_series_matrix.txt.gz"
    assert path.read_bytes() == b"matrix-content"


def test_download_uses_cache_when_exists(tmp_path):
    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=b"data")

    fetcher = GEOFetcher(
        storage=storage,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    fetcher.download("GSE1")
    fetcher.download("GSE1")
    assert calls["n"] == 1  # 第二次命中缓存不再请求


def test_download_404_raises(tmp_path):
    def handler(request):
        return httpx.Response(404)

    from src.data.storage import FetcherStorage

    storage = FetcherStorage(base_dir=str(tmp_path / "raw"), meta_dir=str(tmp_path / "meta"))
    fetcher = GEOFetcher(storage=storage, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        fetcher.download("GSE999999")
    except ValueError:
        assert True
    else:
        raise AssertionError("download 应抛 ValueError")


def test_ingest_text_from_summary():
    def handler(request):
        if "esearch.fcgi" in str(request.url):
            return httpx.Response(200, json=_esearch_response())
        return httpx.Response(200, json=_esummary_response())

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("GSE123456")
    assert "# GEO 数据集: GSE123456" in text
    assert "Expression data" in text
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_geo_fetcher.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.data.fetchers.geo_fetcher'`

- [ ] **步骤 3：实现 GEOFetcher**

创建 `src/data/fetchers/geo_fetcher.py`：

```python
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

    def _series_matrix_url(self, accession: str) -> str:
        """构造 GEO Series Matrix 下载地址"""
        digits = accession[3:]
        prefix = (digits[:3] if len(digits) >= 3 else digits).zfill(3)
        sub = f"GSE{prefix}"
        return (
            f"{self.GEO_FTP_URL}/{sub}/{accession}/matrix/"
            f"{accession}_series_matrix.txt.gz"
        )
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_geo_fetcher.py -v`
预期：PASS（8 tests passed）

- [ ] **步骤 5：Commit**

```bash
git add src/data/fetchers/geo_fetcher.py tests/unit/test_geo_fetcher.py
git commit -m "feat: GEO 数据集 fetcher（分析流，Series Matrix 下载）"
```

---

### 任务 8：包导出与默认注册收尾

**文件：**
- 修改：`src/data/__init__.py`
- 创建：`src/data/fetchers/__init__.py`

- [ ] **步骤 1：创建 fetchers 包导出**

创建 `src/data/fetchers/__init__.py`：

```python
"""公共数据库 fetcher 集合"""
from src.data.fetchers.base import AssetInfo, AssetMeta, BaseFetcher
from src.data.fetchers.geo_fetcher import GEOFetcher
from src.data.fetchers.kegg_fetcher import KEGGFetcher
from src.data.fetchers.uniprot_fetcher import UniProtFetcher

__all__ = [
    "AssetInfo", "AssetMeta", "BaseFetcher",
    "GEOFetcher", "KEGGFetcher", "UniProtFetcher",
]
```

- [ ] **步骤 2：更新 data 包导出**

修改 `src/data/__init__.py` 中内容为：

```python
"""数据层模块"""
from src.data.fetchers import (
    AssetInfo, AssetMeta, BaseFetcher,
    GEOFetcher, KEGGFetcher, UniProtFetcher,
)
from src.data.registry import FetcherRegistry
from src.data.storage import FetcherStorage

__all__ = [
    "AssetInfo", "AssetMeta", "BaseFetcher",
    "GEOFetcher", "KEGGFetcher", "UniProtFetcher",
    "FetcherRegistry", "FetcherStorage",
]
```

- [ ] **步骤 3：验证 registry 默认注册测试通过**

运行：`python -m pytest tests/unit/test_registry.py -v`
预期：PASS（4 tests passed，含之前待验证的 `test_build_default_registers_three`）

- [ ] **步骤 4：验证 smoke 可用**

运行：

```python
python -c "from src.data import FetcherRegistry, GEOFetcher, KEGGFetcher, UniProtFetcher, FetcherStorage; r = FetcherRegistry.build_default(); print(r.sources())"
```

预期：输出 `['kegg', 'uniprot', 'geo']`

- [ ] **步骤 5：Commit**

```bash
git add src/data/__init__.py src/data/fetchers/__init__.py
git commit -m "feat: 数据层包导出与默认注册收尾"
```

---

### 任务 9：真实 API 集成测试（可选）

**文件：**
- 创建：`tests/integration/test_public_db_fetchers.py`

- [ ] **步骤 1：编写集成测试**

创建 `tests/integration/test_public_db_fetchers.py`：

```python
"""真实公共数据库 API 集成测试

默认跳过，需设置 RUN_NETWORK_TESTS=1 才会运行。
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1",
    reason="需真实网络，设置 RUN_NETWORK_TESTS=1 运行",
)


def test_kegg_real_search():
    from src.data.fetchers.kegg_fetcher import KEGGFetcher

    fetcher = KEGGFetcher()
    metas = fetcher.search("glycolysis", max_results=3)
    assert metas
    assert all(m.source == "kegg" for m in metas)


def test_kegg_real_get():
    from src.data.fetchers.kegg_fetcher import KEGGFetcher

    fetcher = KEGGFetcher()
    text = fetcher.ingest_text("hsa00010")
    assert "Glycolysis" in text


def test_uniprot_real_search():
    from src.data.fetchers.uniprot_fetcher import UniProtFetcher

    fetcher = UniProtFetcher()
    metas = fetcher.search("TP53 AND organism_id:9606", max_results=3)
    assert metas
    assert any(m.asset_id == "P04637" for m in metas)


def test_uniprot_real_get():
    from src.data.fetchers.uniprot_fetcher import UniProtFetcher

    fetcher = UniProtFetcher()
    text = fetcher.ingest_text("P04637")
    assert "P04637" in text


def test_geo_real_search():
    from src.data.fetchers.geo_fetcher import GEOFetcher

    fetcher = GEOFetcher()
    metas = fetcher.search("type:gse AND human AND diabetes", max_results=3)
    assert metas
    assert all(m.asset_id.startswith("GSE") for m in metas)
```

- [ ] **步骤 2：确认默认跳过**

运行：`python -m pytest tests/integration/test_public_db_fetchers.py -v`
预期：SKIPPED（5 skipped，默认不访问真实网络）

- [ ] **步骤 3：Commit**

```bash
git add tests/integration/test_public_db_fetchers.py
git commit -m "test: 公共数据库真实 API 集成测试（默认跳过）"
```

---

### 收尾验证

- [ ] **运行全部单元测试**

```bash
python -m pytest tests/unit/test_storage.py tests/unit/test_base_fetcher.py tests/unit/test_registry.py tests/unit/test_kegg_fetcher.py tests/unit/test_uniprot_fetcher.py tests/unit/test_geo_fetcher.py tests/unit/test_lightrag_client.py tests/unit/test_knowledge_importer.py tests/unit/test_intent_parser.py -v
```

预期：原有测试全部 PASS，新增 30 个测试 PASS，无回归。

- [ ] **清理测试残留目录**

```bash
python -c "import shutil; shutil.rmtree('test_storage', ignore_errors=True)"
```

确认 `test_storage/` 已删除。

**完成标志：** 数据获取层可用（`FetcherRegistry.build_default()` 返回 geo/kegg/uniprot 三个 fetcher，KEGG/UniProt 可转知识文本，GEO 可下载 Series Matrix 并缓存）。