# 工作流 / UI / 分析流集成实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 打通「检索 → 列候选 → 用户确认 → 分流」两段式数据获取工作流（M3 + M4 的集成部分）：`IntentParser` 识别 fetch_data 意图；`WorkflowManager` 实现检索-确认-下载与知识库入库；GEO 已下载数据可直接进入 R 差异表达分析流；`MultiomicsAgent` 接线组装；Streamlit UI 展示候选列表并支持确认下载。对应规格第 8 章与里程碑 M3、M4。

**前置：** 数据获取层（计划 1）与知识构建/LightRAG 配置（计划 2）已完成。本计划依赖其公开接口：
- `FetcherRegistry(sources()/get(source))`、`BaseFetcher(search/confirm/download/ingest_text)`、`FetcherStorage(base_dir)`、`AssetMeta/AssetInfo`
- `LightRAGClient`、`KnowledgeBuilder(build_from_text/texts)`

**兼容性约束（必须保持现有测试绿）：**
- `WorkflowManager(intent_parser, knowledge_client, r_executor, visualizer)` 位置参数不变，新组件全部为**可选关键字参数**
- `MultiomicsAgent(config)` 不变；`config['llm']['provider']='mock'` 时不得触发 LightRAG 初始化参数校验（延迟初始化，仅 insert/query 时触发）
- `test_ui.py` / `test_full_workflow.py` / `test_workflow_integration.py` 现有断言不得破坏

**技术栈：** pytest、unittest.mock、Streamlit（UI 手动验证）。所有测试不调用外部 API。

---

### 任务 1：IntentParser 识别 fetch_data 意图与数据来源提取

**文件：**
- 修改：`src/control/intent_parser.py`
- 测试：`tests/unit/test_intent_parser.py`

- [ ] **步骤 1：追加失败测试**

在 `tests/unit/test_intent_parser.py` 追加：

```python
def test_parse_fetch_data_intent():
    """识别数据集下载意图并提取来源与编号"""
    parser = IntentParser()

    intent = parser.parse("帮我下载 GSE123456 数据集")
    assert intent['type'] == 'fetch_data'

    params = parser.extract_parameters("帮我下载 GSE123456 数据集")
    assert 'geo' in params['sources']
    assert 'GSE123456' in params['dataset_ids']


def test_parse_uniprot_query_intent():
    parser = IntentParser()
    intent = parser.parse("下载 TP53 蛋白信息")
    assert intent['type'] == 'fetch_data'
    assert 'uniprot' in parser.extract_parameters("下载 TP53 蛋白信息")['sources']


def test_parse_analysis_priority_over_fetch():
    """含分析关键词时 analysis 优先（如『下载后做差异分析』）"""
    parser = IntentParser()
    intent = parser.parse("下载数据集并做差异表达分析")
    assert intent['type'] == 'analysis'
    assert intent['analysis_type'] == 'differential_expression'
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_intent_parser.py -v`
预期：2 个新测试 FAIL（当前 parse 返回 `general`，extract 无 `sources`/`dataset_ids`）

- [ ] **步骤 3：实现意图识别**

修改 `src/control/intent_parser.py`：

`__init__` 中追加：

```python
        self.dataset_keywords = ['下载', '获取数据', '数据集', '找数据', '数据下载']
        self.source_keywords = {
            'geo': ['geo', 'gse', '数据集', '表达谱'],
            'kegg': ['kegg', 'pathway', '通路'],
            'uniprot': ['uniprot', '蛋白', 'protein'],
        }
```

`parse` 中（analysis 分支之后、knowledge 分支之前）插入：

```python
        # 检查是否为数据获取意图
        for keyword in self.dataset_keywords:
            if keyword in user_input:
                return {
                    'type': 'fetch_data',
                    'confidence': 0.75,
                    'original_input': user_input
                }
```

`extract_parameters` 末尾追加：

```python
        # 提取数据来源
        sources = []
        for source, keywords in self.source_keywords.items():
            if any(k in user_input for k in keywords):
                sources.append(source)
        if sources:
            params['sources'] = sources

        # 提取数据集编号（GSE）
        gse_pattern = r'\bGSE\d+\b'
        gses = re.findall(gse_pattern, user_input.upper())
        if gses:
            params['dataset_ids'] = gses

        return params
```

> 注意：现 `extract_parameters` 尾部 `return params` 需保留，新逻辑插入其前。

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_intent_parser.py -v`
预期：PASS（原 1 个 + 新 3 个测试）

- [ ] **步骤 5：Commit**

```bash
git add src/control/intent_parser.py tests/unit/test_intent_parser.py
git commit -m "feat: 意图解析识别 fetch_data 与数据来源"
```

---

### 任务 2：WorkflowManager 两段式「检索-确认-下载」工作流

**文件：**
- 修改：`src/control/workflow_manager.py`
- 测试：`tests/unit/test_workflow_manager.py`（新建）

- [ ] **步骤 1：抛出失败测试**

创建 `tests/unit/test_workflow_manager.py`：

```python
from pathlib import Path

from src.control.intent_parser import IntentParser
from src.control.workflow_manager import WorkflowManager
from src.data.fetchers.base import AssetInfo, AssetMeta


class MockKnowledgeClient:
    def query(self, query): return "mock"


class MockRExecutor:
    def execute_code(self, code):
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class MockVisualizer:
    pass


class FakeFetcher:
    source = "geo"
    asset_type = "analysis"

    def __init__(self, metas=None):
        self.metas = metas or [
            AssetMeta(asset_id="GSE123456", title="RNA-seq of HCC",
                      source="geo", asset_type="analysis"),
            AssetMeta(asset_id="GSE654321", title="ATAC-seq of HCC",
                      source="geo", asset_type="analysis"),
        ]

    def search(self, query, max_results=20):
        return self.metas[:max_results]

    def confirm(self, asset_id):
        return AssetInfo(asset_id=asset_id, title="RNA-seq of HCC",
                         source="geo", asset_type="analysis",
                         metadata={"organism": "Homo sapiens"})

    def download(self, asset_id):
        return Path("data/raw/geo") / asset_id / "series_matrix.txt"

    def ingest_text(self, asset_id):
        return f"GEO 数据集 {asset_id} 的元数据文本"


class FakeRegistry:
    def __init__(self, fetchers=None):
        self._f = fetchers or {"geo": FakeFetcher()}
        self._fetchers = list(self._f.keys())

    def get(self, source):
        return self._f[source]

    def sources(self):
        return self._fetchers


class FakeBuilder:
    def __init__(self):
        self.received = []
    def build_from_text(self, text):
        self.received.append(text)
        return {"inserted": 1}


def _make_manager(fetcher_registry=FakeRegistry(), builder=None):
    return WorkflowManager(
        intent_parser=IntentParser(),
        knowledge_client=MockKnowledgeClient(),
        r_executor=MockRExecutor(),
        visualizer=MockVisualizer(),
        fetcher_registry=fetcher_registry,
        knowledge_builder=builder or FakeBuilder(),
    )


def test_fetch_data_workflow_needs_confirmation():
    manager = _make_manager()
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result['type'] == 'fetch_data'
    assert result['status'] == 'needs_confirmation'
    assert result['candidates'][0]['asset_id'] == 'GSE123456'


def test_fetch_data_no_registry():
    manager = _make_manager(fetcher_registry=None)
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result['status'] == 'error'


def test_fetch_data_no_results():
    manager = _make_manager(FakeRegistry({"geo": FakeFetcher(metas=[])}))
    result = manager.execute_workflow("帮我下载 GEO 数据集")
    assert result['status'] == 'no_results'


def test_confirm_and_download_success():
    manager = _make_manager()
    result = manager.confirm_and_download("geo", "GSE123456")
    assert result['status'] == 'success'
    assert result['type'] == 'fetch_result'
    assert result['asset']['asset_id'] == 'GSE123456'
    assert str(result['asset']['access_path']).endswith("series_matrix.txt")


def test_ingest_asset_to_kb():
    builder = FakeBuilder()
    manager = _make_manager(builder=builder)
    result = manager.ingest_asset_to_kb("geo", "GSE123456")
    assert result['status'] == 'success'
    assert result['inserted'] == 1
    assert "GSE123456" in builder.received[0]


def test_fetch_data_context_records_candidates():
    manager = _make_manager()
    context = {}
    manager.execute_workflow("帮我下载 GEO 数据集", context=context)
    assert len(context['fetch_candidates']) == 2
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_workflow_manager.py -v`
预期：FAIL，`TypeError: __init__() got an unexpected keyword argument 'fetcher_registry'`

- [ ] **步骤 3：实现两段式工作流**

修改 `src/control/workflow_manager.py`：

`__init__` 签名（追加可选关键字参数，不动现有位置参数）：

```python
    def __init__(self, intent_parser, knowledge_client, r_executor, visualizer,
                 fetcher_registry=None, storage=None, knowledge_builder=None):
        self.intent_parser = intent_parser
        self.knowledge_client = knowledge_client
        self.r_executor = r_executor
        self.visualizer = visualizer
        self.fetcher_registry = fetcher_registry
        self.storage = storage
        self.knowledge_builder = knowledge_builder
```

`execute_workflow` 分支追加：

```python
        elif intent['type'] == 'fetch_data':
            return self._execute_fetch_data_workflow(intent, params, context)
```

新增方法：

```python
    def _execute_fetch_data_workflow(self, intent: dict[str, Any], params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """阶段 1：检索 → 列出候选 → 等待用户确认下载"""
        if self.fetcher_registry is None:
            return {'status': 'error', 'type': 'fetch_data', 'message': '数据获取组件未配置'}
        query = intent.get('original_input', '')
        sources = params.get('sources') or self.fetcher_registry.sources()

        candidates = []
        for source in sources:
            fetcher = self.fetcher_registry.get(source)
            try:
                metas = fetcher.search(query, max_results=5)
            except Exception as e:  # noqa: BLE001 - 单个来源失败不应中断整体检索
                logger.warning("source %s search failed: %s", source, e)
                continue
            for meta in metas:
                candidates.append({
                    'source': meta.source,
                    'asset_id': meta.asset_id,
                    'title': meta.title,
                    'asset_type': meta.asset_type,
                })

        if not candidates:
            return {'status': 'no_results', 'type': 'fetch_data', 'query': query,
                    'message': '未找到匹配的数据集，请换关键词重试'}

        context['fetch_candidates'] = candidates
        return {
            'status': 'needs_confirmation', 'type': 'fetch_data',
            'query': query, 'candidates': candidates,
            'message': f'找到 {len(candidates)} 个候选数据集，请选择要下载的项',
        }

    def confirm_and_download(self, source: str, asset_id: str) -> dict[str, Any]:
        """阶段 2：确认详情 → 下载落盘（分析流入口）"""
        if self.fetcher_registry is None:
            return {'status': 'error', 'message': '数据获取组件未配置'}
        fetcher = self.fetcher_registry.get(source)
        info = fetcher.confirm(asset_id)
        access_path = fetcher.download(asset_id)
        logger.info("Asset confirmed and downloaded: %s/%s -> %s", source, asset_id, access_path)
        return {
            'status': 'success',
            'type': 'fetch_result',
            'asset': {
                'source': source,
                'asset_id': asset_id,
                'title': info.title,
                'access_path': str(access_path),
                'metadata': info.metadata,
            },
        }

    def ingest_asset_to_kb(self, source: str, asset_id: str) -> dict[str, Any]:
        """知识流：将资产文本写入知识库（LightRAG）"""
        if self.fetcher_registry is None or self.knowledge_builder is None:
            return {'status': 'error', 'message': '知识构建组件未配置'}
        fetcher = self.fetcher_registry.get(source)
        text = fetcher.ingest_text(asset_id)
        result = self.knowledge_builder.build_from_text(text)
        return {'status': 'success', 'type': 'ingest_result', 'asset_id': asset_id,
                'inserted': result.get('inserted')}
```

- [ ] **步骤 4：运行新测试并确认现有集成测试无回归**

运行：`python -m pytest tests/unit/test_workflow_manager.py tests/integration/test_workflow_integration.py -v`
预期：新 6 个测试 PASS；`test_workflow_integration` 原 4 个测试 PASS

- [ ] **步骤 5：Commit**

```bash
git add src/control/workflow_manager.py tests/unit/test_workflow_manager.py
git commit -m "feat: WorkflowManager 两段式检索确认下载工作流"
```

---

### 任务 3：GEO → R 差异表达分析流

**文件：**
- 修改：`src/control/workflow_manager.py`
- 测试：`tests/unit/test_workflow_manager.py`

- [ ] **步骤 1：追加失败测试**

在 `tests/unit/test_workflow_manager.py` 追加：

```python
def test_de_analysis_uses_downloaded_asset(tmp_path):
    csv_file = tmp_path / "GSE123456_expression.csv"
    csv_file.write_text("gene,log2FC,pvalue\nTP53,1.5,0.001\n", encoding="utf-8")
    context = {'downloaded_assets': [
        {'source': 'geo', 'asset_id': 'GSE123456', 'access_path': str(csv_file)},
    ]}
    manager = _make_manager()
    result = manager.execute_workflow("对这些数据做差异表达分析", context=context)
    assert result['status'] == 'success'
    assert result['analysis_type'] == 'differential_expression'


def test_de_analysis_without_data_errors():
    manager = _make_manager()
    result = manager.execute_workflow("做差异表达分析")
    assert result['status'] == 'error'
```

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/unit/test_workflow_manager.py -v`
预期：`test_de_analysis_uses_downloaded_asset` FAIL（当前占位实现返回空 results 的 success）

- [ ] **步骤 3：实现差异表达分析流**

`workflow_manager.py` 追加模块级导入：

```python
from src.analysis.r_executor import RExecutor
from src.control.r_script_generator import RScriptGenerator
from src.data.data_loader import DataLoader
```

`__init__` 追加可选参数：

```python
    def __init__(self, intent_parser, knowledge_client, r_executor, visualizer,
                 fetcher_registry=None, storage=None, knowledge_builder=None,
                 r_script_generator=None, data_loader=None):
        ...
        self.r_script_generator = r_script_generator or RScriptGenerator()
        self.data_loader = data_loader or DataLoader()
```

替换 `_execute_analysis_workflow`：

```python
    def _execute_analysis_workflow(self, intent: dict[str, Any], params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """执行分析工作流"""
        analysis_type = intent.get('analysis_type')

        if analysis_type == 'differential_expression':
            return self._execute_de_analysis(params, context)

        return {
            'status': 'success',
            'analysis_type': analysis_type,
            'message': f'已开始执行 {analysis_type} 分析',
            'results': {}
        }

    def _execute_de_analysis(self, params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """差异表达分析：优先使用已确认下载的 GEO 数据"""
        input_file = None
        if params.get('input_files'):
            input_file = params['input_files'][0]
        elif context.get('downloaded_assets'):
            input_file = context['downloaded_assets'][-1].get('access_path')

        if not input_file:
            return {'status': 'error', 'analysis_type': 'differential_expression',
                    'message': '缺少数据文件，请先下载或上传数据'}

        output_file = str(Path(input_file).with_suffix('.de_results.csv'))
        code = self.r_script_generator.generate_code(
            'differential_expression',
            {'input_file': input_file, 'output_file': output_file},
        )
        result = self.r_executor.execute_code(code)
        logger.info("DE analysis finished on %s (returncode=%s)", input_file, result.returncode)
        return {
            'status': 'success',
            'analysis_type': 'differential_expression',
            'message': '差异表达分析完成',
            'results': {'returncode': result.returncode, 'output_file': output_file},
        }
```

> 说明：`Path` 需要 `from pathlib import Path`（文件开头追加）。`r_executor` 对外部 API 的依赖在测试中被 `MockRExecutor` 模拟。

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/unit/test_workflow_manager.py tests/integration/test_workflow_integration.py -v`
预期：全 PASS（workflow_manager 8 个 + workflow_integration 4 个）

- [ ] **步骤 5：Commit**

```bash
git add src/control/workflow_manager.py tests/unit/test_workflow_manager.py
git commit -m "feat: GEO 已下载数据接入 R 差异表达分析流"
```

---

### 任务 4：MultiomicsAgent 接线组装

**文件：**
- 修改：`src/main.py`
- 测试：`tests/integration/test_full_workflow.py`、`tests/integration/test_ui.py`

- [ ] **步骤 1：追加失败测试**

`tests/integration/test_full_workflow.py` 追加：

```python
class FakeFetcher:
    source = "geo"
    asset_type = "analysis"

    def search(self, query, max_results=20):
        from src.data.fetchers.base import AssetMeta
        return [AssetMeta(asset_id="GSE123456", title="RNA-seq of HCC",
                          source="geo", asset_type="analysis")]

    def confirm(self, asset_id):
        from src.data.fetchers.base import AssetInfo
        return AssetInfo(asset_id=asset_id, title="RNA-seq of HCC",
                         source="geo", asset_type="analysis")

    def download(self, asset_id):
        return tempfile_path / "series_matrix.txt"


def test_fetch_data_via_agent():
    """Agent 接线：fetch_data 意图 → 候选 → 确认下载"""
    import tempfile
    from unittest.mock import patch

    from src.data.fetchers.base import AssetInfo, AssetMeta
    from src.main import MultiomicsAgent

    tempfile_path = tempfile.TemporaryDirectory()
    try:
        with patch(
            "src.main.FetcherRegistry",
            autospec=True,
        ) as FakeRegistryCls:
            # 返回注入的 fake registry
            fake = FakeRegistry()
            FakeRegistryCls.return_value = fake

            agent = MultiomicsAgent({"data_dir": tempfile_path.name})
            result = agent.execute_workflow("帮我下载 GEO 数据集")
            assert result['type'] == 'fetch_data'
            assert result['status'] == 'needs_confirmation'
    finally:
        tempfile_path.cleanup()
```

> 简化指引：若 mock `FetcherRegistry` 构造复杂，可将上述测试改为对 `agent.workflow_manager` 注入 fake registry 后调 `agent.confirm_and_download("geo", "GSE123456")` 断言成功。本任务最小目标是 Agent 暴露 `confirm_and_download` 且不破坏既有测试。

- [ ] **步骤 2：运行测试确认失败**

运行：`python -m pytest tests/integration/test_full_workflow.py -v`
预期：FAIL，`AttributeError: 'MultiomicsAgent' object has no attribute 'confirm_and_download'`

- [ ] **步骤 3：实现 Agent 接线**

修改 `src/main.py`：

导入与 `__init__`：

```python
from src.analysis.r_executor import RExecutor
from src.analysis.visualization import Visualizer
from src.control.intent_parser import IntentParser
from src.control.workflow_manager import WorkflowManager
from src.data.registry import FetcherRegistry
from src.data.storage import FetcherStorage
from src.knowledge.knowledge_builder import KnowledgeBuilder
from src.knowledge.lightrag_client import LightRAGClient
```

```python
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

        llm_cfg = self.config.get('llm', {})
        provider = llm_cfg.get('provider')
        lightrag_config = {'provider': provider} if provider not in (None, 'mock') else {}

        self.intent_parser = IntentParser()
        self.knowledge_client = LightRAGClient(
            working_dir=self.config.get('knowledge_dir', './knowledge_base'),
            config=lightrag_config,
        )
        self.r_executor = RExecutor()
        self.visualizer = Visualizer()

        self.storage = FetcherStorage(base_dir=self.config.get('data_dir', 'data/raw'))
        self.fetcher_registry = FetcherRegistry.build_default(storage=self.storage)
        self.knowledge_builder = KnowledgeBuilder(
            self.knowledge_client, fetcher_registry=self.fetcher_registry
        )

        self.workflow_manager = WorkflowManager(
            intent_parser=self.intent_parser,
            knowledge_client=self.knowledge_client,
            r_executor=self.r_executor,
            visualizer=self.visualizer,
            fetcher_registry=self.fetcher_registry,
            storage=self.storage,
            knowledge_builder=self.knowledge_builder,
        )

        logger.info("MultiomicsAgent initialized")
```

新增公开方法：

```python
    def confirm_and_download(self, source: str, asset_id: str) -> dict[str, Any]:
        """确认并下载数据资产（UI/CLI 供用户在候选选择后调用）"""
        return self.workflow_manager.confirm_and_download(source, asset_id)

    def ingest_asset(self, source: str, asset_id: str) -> dict[str, Any]:
        """将资产写入知识库（知识流）"""
        return self.workflow_manager.ingest_asset_to_kb(source, asset_id)
```

- [ ] **步骤 4：运行测试确认通过**

运行：`python -m pytest tests/integration/test_full_workflow.py tests/integration/test_ui.py tests/integration/test_workflow_integration.py -v`
预期：PASS（含 `test_full_analysis_workflow` 原断言；`test_knowledge_query_workflow` 仍被 skip）

- [ ] **步骤 5：Commit**

```bash
git add src/main.py tests/integration/test_full_workflow.py
git commit -m "feat: MultiomicsAgent 组装数据获取与知识构建组件"
```

---

### 任务 5：Streamlit UI 候选选择交互

**文件：**
- 修改：`src/ui/app.py`
- 验证：`tests/integration/test_ui.py`（不新增单测，Streamlit 交互手动验证）

- [ ] **步骤 1：实现 UI 交互**

将 `src/ui/app.py` 中主聊天循环部分替换为：

```python
    # 显示聊天历史
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # 用户输入
    if prompt := st.chat_input("请输入您的问题或分析需求"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.spinner("思考中..."):
            result = agent.execute_workflow(prompt)

        if result.get('type') == 'fetch_data' and result.get('status') == 'needs_confirmation':
            st.session_state.fetch_candidates = result.get('candidates', [])
            st.session_state.awaiting_confirmation = True
            with st.chat_message("assistant"):
                st.markdown(result.get('message', '找到候选数据集，请选择要下载的项：'))
        else:
            _render_chat_result(result)

    if st.session_state.get("awaiting_confirmation"):
        _render_candidate_selector(agent)
```

新增辅助函数（模块级，`create_app` 之前）：

```python
def _render_chat_result(result: dict[str, Any]):
    """按结果类型渲染聊天回复"""
    if result.get('type') == 'knowledge_response':
        response = result.get('response', '无响应')
    elif result.get('type') == 'fetch_result':
        asset = result.get('asset', {})
        response = f"已下载 {asset.get('asset_id')} → `{asset.get('access_path')}`"
    elif result.get('results'):
        st.info(f"分析完成: {result.get('message', '')}")
        response = "分析结果已生成，请查看下方图表。"
    else:
        response = result.get('message', '处理完成')
    with st.chat_message("assistant"):
        st.markdown(response)
    st.session_state.messages.append({"role": "assistant", "content": response})


def _render_candidate_selector(agent: Any):
    """渲染候选数据集选择器与确认下载按钮"""
    with st.chat_message("assistant"):
        candidates = st.session_state.fetch_candidates or []
        if not candidates:
            st.session_state.awaiting_confirmation = False
            return
        label_map = {
            f"[{c['source']}] {c['asset_id']} - {c['title']}": c
            for c in candidates
        }
        choice = st.selectbox("选择要下载的数据集：", list(label_map.keys()))
        if st.button("确认下载"):
            selected = label_map[choice]
            with st.spinner("下载中..."):
                result = agent.confirm_and_download(selected['source'], selected['asset_id'])
            st.session_state.awaiting_confirmation = False
            message = f"已下载 {selected['asset_id']} → `{result['asset']['access_path']}`"
            st.session_state.messages.append({"role": "assistant", "content": message})
            st.rerun()
```

> 注意：`app.py` 已 import `Any`；若冲突调整。`st.rerun()` 后按钮状态由 `awaiting_confirmation=False` 清空。

- [ ] **步骤 2：验证集成测试不回归**

运行：`python -m pytest tests/integration/test_ui.py -v`
预期：PASS（agent 初始化与基础工作流不变）

- [ ] **步骤 3：Commit**

```bash
git add src/ui/app.py tests/integration/test_ui.py
git commit -m "feat: UI 候选数据集选择与确认下载交互"
```

---

### 任务 6：全量回归 + 端到端手动验证

- [ ] **步骤 1：全量测试回归**

```bash
python -m pytest tests/ -v
```

预期：全部 PASS（单元 + 集成）。若有 skip（需要外部配置的用例）属预期。

- [ ] **步骤 2：手动端到端（真实外部 API，可选但推荐）**

```bash
streamlit run src/ui/app.py
```

验证路径：
1. Web 输入「帮我下载 GEO 数据集」→ 出现候选 selectbox → 选择一项 → 点「确认下载」→ 显示 `data/raw/geo/<id>/...` 路径
2. CLI 验证两段式（无需 Streamlit）：

```bash
python -c "
from src.main import MultiomicsAgent
agent = MultiomicsAgent()
r = agent.execute_workflow('帮我下载 KEGG 通路数据')
print(r['status'], len(r.get('candidates', [])))
if r.get('candidates'):
    c = r['candidates'][0]
    print(agent.confirm_and_download(c['source'], c['asset_id'])['status'])
"
```

预期：`needs_confirmation` 与候选列表、随后 `success` 与落盘路径。

- [ ] **步骤 3：Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs: 记录工作流/UI 端到端验证说明" || echo "无文档变更，跳过"
```

---

### 收尾验证

- [ ] **运行全量测试**

```bash
python -m pytest tests/ -v
```

预期：全部 PASS。

**完成标志：** 用户输入「下载/获取数据」类请求时，UI 展示候选列表，选择后下载落盘 `data/raw/<source>/<id>/`；已下载 GEO 数据可无缝进入 R 差异表达分析；资产可一键写入 LightRAG 知识库（知识流）。M3（GEO→R 分析）、M4（工作流/UI 集成）达成。