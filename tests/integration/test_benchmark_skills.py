"""标杆技能集成测试：真实 Registry → Loader → ModalRouter.route() 端到端。

不打真实网络（GEOFetcher.search 由集成测试 monkeypatch）、不依赖真实 LLM
（KGMemory 缺省 mock 态）。route() 的 HITL 门对所有技能生效，须传 script_approved=True。
"""

import asyncio
from pathlib import Path

import pytest

from src.control.kg_memory import KGMemory
from src.control.router import ModalRouter
from src.skills.base import SkillBase, SkillContext
from src.skills.loader import SkillLoader
from src.skills.registry import SkillRegistry
from tests.unit.fakes import RecordingKGMemory

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
_BENCHMARK_SKILLS = (
    "differential_expression",
    "single_cell",
    "spatial",
    "search_datasets",
    "query_knowledge",
)


def _make_router(kg_memory=None) -> ModalRouter:
    registry = SkillRegistry(skills_dir=SKILLS_DIR)
    loader = SkillLoader(registry)
    return ModalRouter(registry, loader, kg_memory=kg_memory)


def test_registry_discovers_benchmark_skills():
    registry = SkillRegistry(skills_dir=SKILLS_DIR)
    names = {m["name"] for m in registry.list_skills()}
    assert set(_BENCHMARK_SKILLS) <= names


@pytest.mark.parametrize("skill_name", _BENCHMARK_SKILLS)
def test_loader_loads_benchmark_skills(skill_name):
    registry = SkillRegistry(skills_dir=SKILLS_DIR)
    loader = SkillLoader(registry)
    cls = loader.load(skill_name)
    assert issubclass(cls, SkillBase)
    assert cls.metadata.name == skill_name


@pytest.mark.parametrize(
    "user_input,skill_name,analysis_type",
    [
        ("对这份数据做差异表达分析", "differential_expression", "differential_expression"),
        ("对这份 scRNA-seq 数据做单细胞聚类", "single_cell", "single_cell"),
        ("分析这个 Visium 空间转录组数据", "spatial", "spatial"),
    ],
)
def test_route_analysis_skills(user_input, skill_name, analysis_type):
    router = _make_router()
    result = router.route(user_input, context={"script_approved": True})
    assert result["status"] == "success"
    assert result["skill"] == skill_name
    out = result["output"]
    assert out["analysis_type"] == analysis_type
    assert out["user_input"] == user_input
    assert out["required_inputs"]
    assert out["plan"]


def test_route_writes_skill_memory():
    """③ 链路在真实技能上闭环：teardown → _record_execution → kg_memory.ingest。"""
    kg = RecordingKGMemory()
    router = _make_router(kg_memory=kg)
    result = router.route("对这份数据做差异表达分析", context={"script_approved": True})
    assert result["status"] == "success"
    assert len(kg.records) == 1
    rec = kg.records[0]
    assert rec.run_id.startswith("skill-")
    assert rec.intent.original_input.startswith("skill:differential_expression")
    assert rec.steps[0].tool == "differential_expression"


def test_analysis_skill_rejects_empty_input():
    registry = SkillRegistry(skills_dir=SKILLS_DIR)
    loader = SkillLoader(registry)
    skill = loader.create_instance("differential_expression")
    ctx = SkillContext(run_id="t", params={"user_input": ""}, artifacts={})
    with pytest.raises(ValueError, match="user_input 不能为空"):
        asyncio.run(skill.execute(ctx))


def test_route_search_datasets_end_to_end(monkeypatch):
    from src.data.fetchers.base import AssetMeta
    from src.data.fetchers.geo_fetcher import GEOFetcher

    def fake_search(self, query, max_results=20):
        return [
            AssetMeta(asset_id="GSE123456", title="测试数据集", source="geo", asset_type="analysis")
        ]

    monkeypatch.setattr(GEOFetcher, "search", fake_search)
    router = _make_router()
    result = router.route("帮我下载 GSE123456 数据集", context={"script_approved": True})
    assert result["status"] == "success"
    assert result["skill"] == "search_datasets"
    out = result["output"]
    assert out["query"] == "帮我下载 GSE123456 数据集"
    assert out["count"] == 1
    assert out["results"][0]["asset_id"] == "GSE123456"


def test_route_query_knowledge_end_to_end(tmp_path):
    kg = KGMemory(working_dir=tmp_path / "kg")
    try:
        router = _make_router(kg_memory=kg)
        result = router.route("查询基因功能", context={"script_approved": True})
        assert result["status"] == "success"
        assert result["skill"] == "query_knowledge"
        out = result["output"]
        assert out["question"] == "查询基因功能"
        assert isinstance(out["answer"], str) and out["answer"]
    finally:
        kg.close()


def test_route_query_knowledge_without_kg_memory():
    router = _make_router()
    result = router.route("查询基因功能", context={"script_approved": True})
    assert result["status"] == "success"
    assert result["skill"] == "query_knowledge"
    out = result["output"]
    assert out["question"] == "查询基因功能"
    assert out["answer"] is None
    assert out["reason"] == "知识库未配置"


@pytest.mark.parametrize(
    ("user_input", "analysis_type"),
    [
        ("对这份数据做差异表达分析", "differential_expression"),
        ("分析这个 Visium 空间转录组数据", "spatial"),
    ],
)
def test_production_plan_read_has_no_execution_side_effect(user_input: str, analysis_type: str):
    """生产接线：读取方案不得执行技能生命周期或发布 KG 事实。"""
    import json
    from types import SimpleNamespace

    from src.control.agent_runtime import AgentRuntime

    class _ScriptedLLM:
        """回放一次 run_analysis tool-call，避免依赖真实 LLM。"""

        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        def _create(self, **kwargs):
            tc = SimpleNamespace(
                id="call_run_analysis",
                type="function",
                function=SimpleNamespace(
                    name="run_analysis",
                    arguments=json.dumps({"analysis_type": analysis_type}, ensure_ascii=False),
                ),
            )
            message = SimpleNamespace(content=None, tool_calls=[tc])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class _StubWM:
        """WorkflowManager 面替身：只记录下发的 analysis params 并回 needs_input 终态。"""

        def __init__(self):
            self.params = None

        def run_analysis_for_agent(self, analysis_type, params, context):
            self.params = params
            return {"status": "needs_input", "type": "analysis", "message": "缺输入"}

    kg = RecordingKGMemory()
    router = _make_router(kg_memory=kg)
    wm = _StubWM()
    runtime = AgentRuntime(
        llm_client=_ScriptedLLM(),
        model="fake",
        workflow_manager=wm,
        router=router,
    )

    result = runtime.execute(user_input, context={"downloaded_assets": []})

    # 真实分析仍由 WorkflowManager 面负责（needs_input 是 TERMINAL_STATUSES 之一）
    assert result["status"] == "needs_input"
    # 技能静态方案进了生产 analysis params
    plan = wm.params["skill_plan"]
    assert plan["analysis_type"] == analysis_type
    assert plan["plan"] and plan["required_inputs"]
    # 方案读取不是分析执行，不能把方案字段登记成执行产物。
    assert kg.records == []
