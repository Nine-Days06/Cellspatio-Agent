# 知识构建与 LightRAG 配置实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 LightRAG 真正可用：接上云端 DeepSeek（实体抽取/问答）与本地 Ollama bge-m3（向量），并把批量构建从「逐条插入」改造为「批量插入 + fetcher 接入」，打通文献批量导入。对应规格第 5、6 章与里程碑 M2。

**架构：** 新增 `llm_factory.py` 统一构建 `llm_model_func`（openai/zhipu 兼容）与 `embedding_func`（Ollama bge-m3）。`LightRAGClient` 用真实配置初始化 LightRAG，新增并发安全的批量 `insert_documents(list)` 与 embedding 锁定校验。`KnowledgeImporter` 全部改为批量；`KnowledgeBuilder` 接入 fetcher（KEGG/UniProt/GEO 元数据），弃用 `build_from_pubmed`。

**技术栈：** lightrag-hku 1.5.7、httpx mock、pytest。前置：已按计划 `docs/superpowers/plans/2026-09-20-data-fetchers.md` 完成数据获取层。

**外部依赖：** 端到端验证需要 `ollama pull bge-m3` 与 `DEEPSEEK_API_KEY`；单元测试全部 mock，不发起真实请求。

---

### 任务 1：LLM 与 embedding 函数工厂

**文件：**
- 创建：`src/knowledge/llm_factory.py`
- 测试：`tests/unit/test_llm_factory.py`

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_llm_factory.py`：

```python
import pytest

from src.knowledge.llm_factory import build_embedding_func, build_llm_func


@pytest.mark.asyncio
async def test_build_llm_func_calls_openai(monkeypatch):
    calls = {}

    async def fake_cache(api_key, model, messages, base_url=None, **kwargs):
        calls["api_key"] = api_key
        calls["model"] = model
        calls["base_url"] = base_url
        return "ok"

    monkeypatch.setattr("lightrag.llm.openai.openai_complete_if_cache", fake_cache)

    llm_func, model = build_llm_func("deepseek")
    result = await llm_func("deepseek-v4-flash", [{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert calls["model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_build_llm_func_zhipu_uses_openai_compat(monkeypatch):
    monkeypatch.setattr("src.config.LLM_PROVIDER_CONFIGS", {
        "zhipu": {
            "api_key": "", "api_key_env": "ZHIPU_API_KEY",
            "client_type": "zhipuai", "model": "glm-4", "base_url": None,
            "extra_kwargs": {},
        },
    })
    calls = {}

    async def fake_cache(api_key, model, messages, base_url=None, **kwargs):
        calls["base_url"] = base_url
        return "ok"

    monkeypatch.setattr("lightrag.llm.zhipu.zhipu_complete_if_cache", fake_cache)

    llm_func, model = build_llm_func("zhipu")
    assert model == "glm-4"
    await llm_func("glm-4", [])
    assert calls["base_url"].startswith("https://open.bigmodel.cn")


def test_build_embedding_func_default_is_ollama_bge():
    embedding = build_embedding_func()
    assert getattr(embedding, "model_name", "") == "bge-m3:latest"
    assert getattr(embedding, "embedding_dim", 0) == 1024


def test_build_embedding_func_passthrough_custom():
    custom = object()
    assert build_embedding_func(embedding_func=custom) is custom
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_llm_factory.py -v`
预期：FAIL，`ModuleNotFoundError: No module named 'src.knowledge.llm_factory'`

- [ ] **步骤 3：实现 llm_factory**

创建 `src/knowledge/llm_factory.py`：

```python
"""LightRAG 的 LLM 与 embedding 函数工厂"""
import logging

logger = logging.getLogger(__name__)


def build_llm_func(provider: str | None = None) -> tuple:
    """按 src.config 供应商配置构建 async LLM 函数与默认模型名

    openai 兼容供应商（deepseek/openai）走 openai_complete_if_cache；
    zhipu 走 zhipu_complete_if_cache（遵循其官方 base_url）。
    返回值：(llm_func, model_name)
    """
    from src.config import get_llm_config

    config = get_llm_config(provider)
    api_key = config["api_key"]
    model = config["model"]
    base_url = config.get("base_url")

    if config["client_type"] == "zhipuai":
        from lightrag.llm.zhipu import zhipu_complete_if_cache

        base_url = base_url or "https://open.bigmodel.cn/api/paas/v4"

        async def llm_func(model_name: str, messages: list[dict], **kwargs) -> str:
            return await zhipu_complete_if_cache(
                api_key=api_key, model=model_name, messages=messages,
                base_url=base_url, **kwargs,
            )
    else:
        from lightrag.llm.openai import openai_complete_if_cache

        async def llm_func(model_name: str, messages: list[dict], **kwargs) -> str:
            return await openai_complete_if_cache(
                api_key=api_key, model=model_name, messages=messages,
                base_url=base_url, **kwargs,
            )

    return llm_func, model


def build_embedding_func(embedding_func=None):
    """返回 LightRAG 可用的 embedding 函数

    默认使用 Ollama bge-m3（lightrag.llm.ollama.ollama_embed 内置的
    EmbeddingFunc，1024 维、model_name='bge-m3:latest'）。
    调用方可通过 OLLAMA_HOST 或 config['ollama_url'] 指向本地 Ollama。
    传入自定义 embedding_func 时原样返回。
    """
    if embedding_func is not None:
        return embedding_func
    from lightrag.llm.ollama import ollama_embed

    return ollama_embed
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_llm_factory.py -v`
预期：PASS（4 tests passed）

- [ ] **步骤 5：Commit**

```bash
git add src/knowledge/llm_factory.py tests/unit/test_llm_factory.py
git commit -m "feat: LightRAG 的 LLM 与 embedding 函数工厂"
```

---

### 任务 2：LightRAGClient 真实配置与批量插入

**文件：**
- 修改：`src/knowledge/lightrag_client.py`
- 测试：`tests/unit/test_lightrag_client.py`

- [ ] **步骤 1：扩充测试**

在 `tests/unit/test_lightrag_client.py` 追加：

```python
class FakeRAG:
    def __init__(self):
        self.inserted = []
    def insert(self, input_data):
        if isinstance(input_data, list):
            self.inserted.extend(input_data)
        else:
            self.inserted.append(input_data)


def _client_with_fake_rag(working_dir, config=None):
    from src.knowledge.lightrag_client import LightRAGClient

    client = LightRAGClient(working_dir=working_dir, config=config or {})
    client._rag = FakeRAG()
    return client


def test_initialize_rag_uses_configured_models(tmp_path, monkeypatch):
    from src.knowledge.lightrag_client import LightRAGClient

    captured = {}

    class FakeRagForInit:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("lightrag.LightRAG", FakeRagForInit)
    monkeypatch.setattr(
        "src.knowledge.llm_factory.build_llm_func",
        lambda provider: (object(), "deepseek-v4-flash"),
    )
    monkeypatch.setattr(
        "src.knowledge.llm_factory.build_embedding_func",
        lambda custom=None: object(),
    )

    client = LightRAGClient(working_dir=tmp_path, config={"ollama_url": "http://127.0.0.1:11434"})
    client._initialize_rag()
    assert captured["llm_model_name"] == "deepseek-v4-flash"
    assert captured["embedding_func"] is not None


def test_insert_documents_batch(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    count = client.insert_documents(["doc-a", "doc-b"])
    assert count == 2
    assert client._rag.inserted == ["doc-a", "doc-b"]


def test_insert_documents_empty_returns_zero(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    assert client.insert_documents([]) == 0


def test_insert_document_single_still_works(tmp_path):
    client = _client_with_fake_rag(tmp_path)
    client.insert_document("single-doc")
    assert client._rag.inserted == ["single-doc"]
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_lightrag_client.py -v`
预期：FAIL，`AttributeError: ... insert_documents`（方法不存在）

- [ ] **步骤 3：改造 LightRAGClient**

修改 `src/knowledge/lightrag_client.py` 中 `_initialize_rag`、新增 `insert_documents`：

```python
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LightRAGClient:
    """LightRAG 客户端封装"""

    def __init__(self, working_dir: str, config: dict[str, Any] | None = None):
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(exist_ok=True)
        self.config = config or {}

        # 延迟导入 LightRAG，避免立即依赖
        self._rag = None

    def _initialize_rag(self):
        """使用真实 LLM（云端 DeepSeek 等）与 embedding（本地 Ollama bge-m3）初始化"""
        if self._rag is None:
            try:
                from lightrag import LightRAG

                ollama_url = self.config.get("ollama_url") or os.getenv("OLLAMA_HOST")
                if ollama_url:
                    os.environ["OLLAMA_HOST"] = ollama_url.rstrip("/")
                os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")

                from src.knowledge.llm_factory import build_embedding_func, build_llm_func

                llm_func, llm_model = build_llm_func(self.config.get("provider"))
                embedding_func = build_embedding_func(self.config.get("embedding_func"))
                self._validate_embedding_model(embedding_func)

                self._rag = LightRAG(
                    working_dir=str(self.working_dir),
                    llm_model_func=llm_func,
                    llm_model_name=llm_model,
                    embedding_func=embedding_func,
                    enable_llm_cache=True,
                )
                logger.info("LightRAG initialized: llm=%s embedding=%s",
                            llm_model, getattr(embedding_func, "model_name", "custom"))
            except ImportError:
                logger.error("LightRAG not installed. Install with: pip install lightrag-hku")
                raise

    def insert_document(self, document: str, metadata: dict[str, Any] | None = None):
        """增量插入单篇文档"""
        self._initialize_rag()
        if self._rag:
            self._rag.insert(document)
            logger.info(f"Document inserted, length: {len(document)}")

    def insert_documents(self, documents: list[str]) -> int:
        """批量插入文档（批量构建与导入统一走这里），返回插入数量"""
        self._initialize_rag()
        if not documents or self._rag is None:
            return 0
        self._rag.insert(documents)
        logger.info(f"Batch inserted {len(documents)} documents")
        return len(documents)

    def query(self, question: str, mode: str = "hybrid") -> str:
        """查询知识库"""
        self._initialize_rag()
        if self._rag:
            return self._rag.query(question, param={"mode": mode})
        return "LightRAG 未初始化"

    def insert_knowledge_graph(self, kg_data: dict[str, Any]):
        """插入知识图谱数据"""
        self._initialize_rag()
        if self._rag:
            # LightRAG 支持自定义知识图谱插入
            self._rag.insert_custom_kg(kg_data)

    def get_statistics(self) -> dict[str, Any]:
        """获取知识库统计信息"""
        return {
            "working_dir": str(self.working_dir),
            "initialized": self._rag is not None,
        }

    def _validate_embedding_model(self, embedding_func):
        """embedding 锁定校验：一旦建库写入模型标识，后续必须一致"""
        model_name = getattr(embedding_func, "model_name", None) or "custom"
        lock_file = self.working_dir / "EMBEDDING_MODEL.json"
        if lock_file.exists():
            saved = json.loads(lock_file.read_text(encoding="utf-8")).get("model")
            if saved != model_name:
                raise RuntimeError(
                    f"embedding 模型已锁定为 {saved}，不能改为 {model_name}；如需更换需清空知识库重建"
                )
        else:
            lock_file.write_text(
                json.dumps({"model": model_name}, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("embedding 模型已锁定: %s", model_name)
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_lightrag_client.py -v`
预期：PASS（原 2 个 + 新增 5 个测试通过）

- [ ] **步骤 5：Commit**

```bash
git add src/knowledge/lightrag_client.py tests/unit/test_lightrag_client.py
git commit -m "feat: LightRAGClient 接入真实 LLM/embedding 与批量插入"
```

---

### 任务 3：KnowledgeImporter 批量导入改造

**文件：**
- 修改：`src/knowledge/knowledge_importer.py`
- 测试：`tests/unit/test_knowledge_importer.py`
- 修改：`tests/integration/test_literature_import.py`

- [ ] **步骤 1：适配单元测试的 Mock 客户端**

修改 `tests/unit/test_knowledge_importer.py` 中 `MockLightRAGClient`：

```python
class MockLightRAGClient:
    def __init__(self):
        self.inserted_documents = []
    def insert_document(self, document: str):
        self.inserted_documents.append(document)
    def insert_documents(self, documents: list[str]) -> int:
        self.inserted_documents.extend(documents)
        return len(documents)
```

`test_import_from_json` 断言不变（仍 `len(client.inserted_documents) == 1`），其余测试不变。

- [ ] **步骤 2：更新集成测试的 Mock 客户端**

修改 `tests/integration/test_literature_import.py` 中 `MockLightRAGClient`，追加：

```python
    def insert_documents(self, documents: list[str]) -> int:
        for doc in documents:
            self.insert_document(doc)
        return len(documents)
```

- [ ] **步骤 3：运行现有测试（确认目前仍绿，随后改造会变红）**

运行：`python -m pytest tests/unit/test_knowledge_importer.py tests/integration/test_literature_import.py -v`
预期：PASS（改造前基线）

- [ ] **步骤 4：改造 KnowledgeImporter 为批量导入**

修改 `src/knowledge/knowledge_importer.py` 三个导入方法内层循环：

`import_from_json` 中替换：

```python
            articles = data if isinstance(data, list) else data.get("articles", [])

            texts = [self._convert_article_to_text(a) for a in articles]
            count = self.client.insert_documents(texts)

            logger.info(f"Imported {count} articles from JSON: {json_path}")
            return {"success": True, "count": count, "source": json_path}
```

`import_from_sqlite` 中替换：

```python
            texts = [self._convert_article_to_text(row.to_dict()) for _, row in df.iterrows()]
            count = self.client.insert_documents(texts)

            logger.info(f"Imported {count} articles from SQLite: {db_path}")
            return {"success": True, "count": count, "source": db_path}
```

`import_from_csv` 中替换：

```python
            texts = [self._convert_article_to_text(row.to_dict()) for _, row in df.iterrows()]
            count = self.client.insert_documents(texts)

            logger.info(f"Imported {count} articles from CSV: {csv_path}")
            return {"success": True, "count": count, "source": csv_path}
```

- [ ] **步骤 5：运行测试确认通过**

运行：`python -m pytest tests/unit/test_knowledge_importer.py tests/integration/test_literature_import.py -v`
预期：PASS（全部通过，断言仍成立）

- [ ] **步骤 6：Commit**

```bash
git add src/knowledge/knowledge_importer.py tests/unit/test_knowledge_importer.py tests/integration/test_literature_import.py
git commit -m "feat: KnowledgeImporter 改为批量导入"
```

---

### 任务 4：KnowledgeBuilder 接入 fetcher 与批量构建

**文件：**
- 修改：`src/knowledge/knowledge_builder.py`
- 测试：`tests/unit/test_knowledge_builder.py`（新建）

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_knowledge_builder.py`：

```python
from src.knowledge.knowledge_builder import KnowledgeBuilder


class MockLightRAGClient:
    def __init__(self):
        self.inserted_documents = []
    def insert_documents(self, documents) -> int:
        self.inserted_documents.extend(documents)
        return len(documents)


class FakeFetcher:
    def __init__(self, source):
        self.source = source
        self.calls = []
    def ingest_text(self, asset_id) -> str:
        self.calls.append(asset_id)
        return f"# text of {self.source}:{asset_id}"


def test_build_from_kegg_batch():
    fetcher = FakeFetcher("kegg")
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_kegg(["hsa00010", "hsa00020"], fetcher=fetcher)
    assert result["inserted"] == 2
    assert fetcher.calls == ["hsa00010", "hsa00020"]


def test_build_from_uniprot_batch():
    fetcher = FakeFetcher("uniprot")
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_uniprot(["P04637"], fetcher=fetcher)
    assert result["inserted"] == 1
    assert "P04637" in fetcher.calls


def test_build_from_geo_metadata():
    fetcher = FakeFetcher("geo")
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_geo_metadata(["GSE123456"], fetcher=fetcher)
    assert result["inserted"] == 1


def test_build_from_articles_batch():
    client = MockLightRAGClient()
    builder = KnowledgeBuilder(client)
    articles = [
        {"pmid": "1", "title": "A", "abstract": "abs"},
        {"pmid": "2", "title": "B", "abstract": "abs"},
    ]
    result = builder.build_from_articles(articles)
    assert result == {"inserted": 2}
    assert len(client.inserted_documents) == 2


def test_build_initial_dispatch_sources(monkeypatch):
    client = MockLightRAGClient()
    builder = KnowledgeBuilder(client)

    fetchers = {"kegg": FakeFetcher("kegg"), "uniprot": FakeFetcher("uniprot")}

    class FakeRegistry:
        def get(self, source):
            return fetchers[source]

    monkeypatch.setattr(builder, "registry", FakeRegistry())
    config = {
        "sources": [
            {"type": "kegg", "pathway_ids": ["hsa00010"]},
            {"type": "uniprot", "accessions": ["P04637"]},
        ]
    }
    result = builder.build_initial_knowledge_base(config)
    assert result["inserted"] == 2
    assert result["sources"] == ["kegg", "uniprot"]


def test_build_from_pubmed_deprecated():
    builder = KnowledgeBuilder(MockLightRAGClient())
    result = builder.build_from_pubmed(["multi-omics"])
    assert result["inserted"] == 0
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_knowledge_builder.py -v`
预期：FAIL，多个断言失败（现有 build_from_* 行为不一致）

- [ ] **步骤 3：改造 KnowledgeBuilder**

将 `src/knowledge/knowledge_builder.py` 替换为：

```python
import logging
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeBuilder:
    """知识库构建器，从多源数据批量构建知识库

    文献走 KnowledgeImporter（pubmed-etl 产物）；
    公共数据库走 data/fetchers（KEGG / UniProt / GEO 元数据）。
    """

    def __init__(self, lightrag_client, fetcher_registry=None):
        self.client = lightrag_client
        self.registry = fetcher_registry

    def _need_registry(self):
        if self.registry is None:
            from src.data.registry import FetcherRegistry
            self.registry = FetcherRegistry.build_default()

    def _insert_texts(self, texts: list[str]) -> int:
        return self.client.insert_documents(texts)

    def build_from_pubmed(self, search_terms: list[str] | None = None, max_per_term: int = 500) -> dict[str, Any]:
        """弃用：文献唯一通道为 pubmed-etl 产物经 KnowledgeImporter 导入"""
        logger.warning("build_from_pubmed 已弃用：请使用 pubmed-etl 导出 + KnowledgeImporter")
        return {"inserted": 0}

    def build_from_kegg(self, pathway_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 KEGG 通路批量构建"""
        self._need_registry()
        fetcher = fetcher or self.registry.get("kegg")
        texts = [fetcher.ingest_text(pid) for pid in pathway_ids]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from KEGG: {len(pathway_ids)} pathways, inserted {inserted}")
        return {"requested": len(pathway_ids), "inserted": inserted}

    def build_from_uniprot(self, accessions: list[str], fetcher=None) -> dict[str, Any]:
        """从 UniProt 蛋白条目批量构建"""
        self._need_registry()
        fetcher = fetcher or self.registry.get("uniprot")
        texts = [fetcher.ingest_text(acc) for acc in accessions]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from UniProt: {len(accessions)} proteins, inserted {inserted}")
        return {"requested": len(accessions), "inserted": inserted}

    def build_from_geo_metadata(self, gse_ids: list[str], fetcher=None) -> dict[str, Any]:
        """从 GEO 数据集元数据批量构建（供「找数据集」类问答）"""
        self._need_registry()
        fetcher = fetcher or self.registry.get("geo")
        texts = [fetcher.ingest_text(gse) for gse in gse_ids]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from GEO metadata: {len(gse_ids)}, inserted {inserted}")
        return {"requested": len(gse_ids), "inserted": inserted}

    def build_from_files(self, file_paths: list[str]) -> dict[str, Any]:
        """从本地文本文件批量构建"""
        texts = []
        for file_path in file_paths:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    texts.append(f.read())
            except (OSError, UnicodeDecodeError) as e:
                logger.error(f"Failed to process file {file_path}: {e}")
        inserted = self._insert_texts(texts)
        return {"requested": len(file_paths), "inserted": inserted}

    def build_from_text(self, text_content: str, metadata: dict[str, Any] | None = None):
        """从单段文本构建"""
        inserted = self._insert_texts([text_content])
        logger.info(f"Built knowledge from text, length: {len(text_content)}")
        return {"inserted": inserted}

    def build_from_articles(self, articles: list[dict[str, Any]]) -> dict[str, Any]:
        """从文章列表批量构建"""
        texts = [self._convert_article_to_text(a) for a in articles]
        inserted = self._insert_texts(texts)
        logger.info(f"Built knowledge from {len(articles)} articles")
        return {"inserted": inserted}

    def build_initial_knowledge_base(self, config: dict[str, Any]) -> dict[str, Any]:
        """按 sources 批量构建初始知识库"""
        sources = config.get('sources', [])
        inserted_total = 0
        handled = []
        for source in sources:
            source_type = source.get('type')
            if source_type == 'pubmed':
                logger.warning("source 'pubmed' 已弃用，请使用 pubmed-etl + KnowledgeImporter")
                continue
            if source_type == 'kegg':
                result = self.build_from_kegg(source.get('pathway_ids', []))
            elif source_type == 'uniprot':
                result = self.build_from_uniprot(source.get('accessions', []))
            elif source_type == 'geo_metadata':
                result = self.build_from_geo_metadata(source.get('gse_ids', []))
            elif source_type == 'files':
                result = self.build_from_files(source.get('file_paths', []))
            else:
                logger.warning(f"未知 source type: {source_type}")
                continue
            inserted_total += result.get("inserted", 0)
            handled.append(source_type)
        logger.info(f"Initial knowledge base built: {inserted_total} documents from {handled}")
        return {"inserted": inserted_total, "sources": handled}

    def _convert_article_to_text(self, article: dict[str, Any]) -> str:
        """将文章转换为 LightRAG 可接受的文本格式"""
        text_parts = []

        if article.get("title"):
            text_parts.append(f"标题：{article['title']}")
        if article.get("abstract"):
            text_parts.append(f"摘要：{article['abstract']}")

        keywords = article.get("keywords", "")
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        elif isinstance(keywords, list):
            pass
        else:
            keywords = []
        if keywords:
            text_parts.append(f"关键词：{', '.join(keywords)}")

        mesh_terms = article.get("mesh_terms", "")
        if isinstance(mesh_terms, str):
            mesh_terms = [m.strip() for m in mesh_terms.split(",") if m.strip()]
        elif isinstance(mesh_terms, list):
            pass
        else:
            mesh_terms = []
        if mesh_terms:
            text_parts.append(f"MeSH词：{', '.join(mesh_terms)}")

        authors = article.get("authors", "")
        if isinstance(authors, str):
            authors = [a.strip() for a in authors.split(",") if a.strip()]
        elif isinstance(authors, list):
            pass
        else:
            authors = []
        if authors:
            text_parts.append(f"作者：{', '.join(authors)}")

        if article.get("year"):
            text_parts.append(f"年份：{article['year']}")
        if article.get("journal"):
            text_parts.append(f"期刊：{article['journal']}")
        if article.get("omics_type"):
            text_parts.append(f"组学类型：{article['omics_type']}")
        if article.get("pmid"):
            text_parts.append(f"PMID：{article['pmid']}")

        return "\n".join(text_parts)

    def get_build_statistics(self) -> dict[str, Any]:
        """获取知识库构建统计信息"""
        return {
            "lightrag_initialized": self.client is not None,
        }
```

> 说明：原 `build_from_pubmed`（占位符）、`_identify_omics_type` 一并移除；`_convert_article_to_text` 保留（importer 与 builder 各自持有，互不影响）。

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_knowledge_builder.py -v`
预期：PASS（6 tests passed）

- [ ] **步骤 5：确认其余测试无回归**

运行：`python -m pytest tests/unit/ -v`
预期：PASS（含 storage/base/registry/fetchers/lightrag_client/importer 全部既有测试）

- [ ] **步骤 6：Commit**

```bash
git add src/knowledge/knowledge_builder.py tests/unit/test_knowledge_builder.py
git commit -m "feat: KnowledgeBuilder 接入 fetcher 批量构建并弃用 pubmed"
```

---

### 任务 5：端到端手动验证（真实环境）

**前置条件：**
- `ollama pull bge-m3`（本地已启动 Ollama）
- `DEEPSEEK_API_KEY` 已在 `.env` 配置

- [ ] **步骤 1：验证 KEGG 通路入库并查询**

运行：

```bash
python -c "
from pathlib import Path
import shutil, tempfile
from src.knowledge.knowledge_builder import KnowledgeBuilder
from src.knowledge.knowledge_importer import KnowledgeImporter
from src.knowledge.lightrag_client import LightRAGClient

workdir = Path(tempfile.mkdtemp())
client = LightRAGClient(str(workdir), config={'ollama_url': 'http://localhost:11434'})
builder = KnowledgeBuilder(client)
print(builder.build_from_kegg(['hsa00010', 'hsa00020', 'hsa00040']))
print(client.query('糖酵解通路涉及哪些关键基因？'))
shutil.rmtree(workdir, ignore_errors=True)
"
```

预期：`inserted` 为 3；`query` 返回基于知识图谱的回答（含实体）。首次运行会索引，耗时较长属正常。

- [ ] **步骤 2：验证嵌入锁定生效**

运行：

```bash
python -c "
from src.knowledge.lightrag_client import LightRAGClient
import shutil, tempfile
workdir = tempfile.mkdtemp()
client = LightRAGClient(workdir, config={'ollama_url': 'http://localhost:11434'})
client.insert_documents(['第一批文本'])
# 人为模拟模型变更
class Other: pass
try:
    client._validate_embedding_model(Other())
except RuntimeError as e:
    print('锁定生效:', str(e)[:40])
shutil.rmtree(workdir, ignore_errors=True)
"
```

预期：第一次写入锁定 `bge-m3:latest`；第二次传入无 model_name 的函数抛 `RuntimeError`（模型已锁定）。

- [ ] **步骤 3：Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs: 记录知识构建端到端验证命令" || echo "无文档变更，跳过"
```

> 若无需提交任何内容可跳过此步。

---

### 收尾验证

- [ ] **运行全部单元测试**

```bash
python -m pytest tests/unit/ -v
```

预期：全部 PASS，无回归。

**完成标志：** LightRAG 使用云端 DeepSeek（EXTRACT/QUERY/KEYWORDS）与本地 Ollama bge-m3（1024 维向量）；`KnowledgeImporter` 批量导入 pubmed-etl 文献；`KnowledgeBuilder` 可批量构建 KEGG/UniProt/GEO 元数据并完成端到端问答验证。