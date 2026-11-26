"""WorkflowManager 文献证据入口：只读检索、失败不冒充空结果。"""

from __future__ import annotations

from typing import Any

from src.control.workflow_manager import WorkflowManager


class FakeKnowledge:
    """最小 LightRAG 客户端桩：回放既定结果或抛出既定异常。"""

    def __init__(self, result=None, error=None, fail_times=None):
        self.result = result
        self.error = error
        self.fail_times = fail_times
        self.calls: list[dict[str, Any]] = []

    def query_data(self, question, mode="mix", **kwargs):
        """记录调用参数；按构造时的 error/result 返回。"""
        self.calls.append({"question": question, "mode": mode, **kwargs})
        if self.error and (self.fail_times is None or len(self.calls) <= self.fail_times):
            raise self.error
        return self.result


def _retrieval(chunks=None):
    """构造成功检索的 LightRAG 形状。"""
    return {
        "status": "success",
        "data": {"chunks": chunks if chunks is not None else [_chunk()]},
        "metadata": {"query_mode": "mix"},
    }


def _chunk(pid="38947524"):
    """单条可核对来源的 chunk。"""
    return {
        "chunk_id": f"chunk-{pid}",
        "reference_id": "1",
        "file_path": pid,
        "content": f"标题：A paper\n摘要：Some evidence.\n年份：2024\nPMID：{pid}",
    }


def _manager(knowledge_client):
    """真实构造，其余依赖置空（该入口只读，不触达 R/可视化）。"""
    return WorkflowManager(
        intent_parser=None,
        knowledge_client=knowledge_client,
        r_executor=None,
        visualizer=None,
    )


def test_search_knowledge_builds_reranker_only_when_requested(monkeypatch):
    """未开启时零构造；开启时只构造一次，两次重试共用同一实例。"""
    built: list[object] = []
    scored: list[object] = []

    class FakeReranker:
        fingerprint = "fake"

        def score(self, query, passages):
            scored.append(self)
            return [0.0] * len(passages)

    def fake_build():
        reranker = FakeReranker()
        built.append(reranker)
        return reranker

    monkeypatch.setattr("src.knowledge.rerank.build_reranker", fake_build)
    manager = _manager(FakeKnowledge(_retrieval(), error=RuntimeError("blip"), fail_times=1))

    manager.search_knowledge_for_agent("TP53")
    assert built == []

    manager.search_knowledge_for_agent("TP53", context={"rerank_enabled": True})
    assert len(built) == 1
    assert scored and scored[0] is built[0]


def test_search_knowledge_returns_evidence_and_continues():
    """成功检索返回证据、来源与条数，并允许模型继续推理。"""
    result = _manager(FakeKnowledge(_retrieval())).search_knowledge_for_agent("TP53")

    assert result["status"] == "success"
    assert result["type"] == "knowledge_evidence"
    assert result["evidence_count"] == len(result["evidence"]) == 1
    assert result["scope"] == "answer"
    assert result["source_count"] == 1
    assert result["rerank_status"] == "disabled"
    assert result["evidence"][0]["evidence_id"] == "E1"
    assert result["continue_tool_loop"] is True
    assert result["message"] == ""


def test_search_knowledge_empty_retrieval_is_not_failure():
    """成功但零条证据是“没有检索到”，不是服务故障。"""
    result = _manager(FakeKnowledge(_retrieval(chunks=[]))).search_knowledge_for_agent("未知")

    assert result["status"] == "success"
    assert result["evidence_count"] == 0
    assert "没有检索到相关证据" in result["message"]


def test_search_knowledge_no_results_status_is_reported():
    """LightRAG 的 no_results 事实透传为 retrieval_status。"""
    payload = {
        "status": "failure",
        "metadata": {"failure_reason": "no_results"},
        "data": {"chunks": []},
    }
    result = _manager(FakeKnowledge(payload)).search_knowledge_for_agent("未知")

    assert result["status"] == "success"
    assert result["retrieval_status"] == "no_results"


def test_search_knowledge_failure_does_not_claim_no_evidence():
    """服务故障重试一次后返回不可用状态，不附带证据、不伪装成空检索。"""
    client = FakeKnowledge(error=RuntimeError("down"))
    result = _manager(client).search_knowledge_for_agent("TP53")

    assert len(client.calls) == 2
    assert result["status"] == "error"
    assert result["outcome"] == "knowledge_unavailable"
    assert result["retrieval_error_reason"] == "call_failed"
    assert "没有检索到" not in result["message"]
    assert "evidence" not in result


def test_search_knowledge_client_missing_is_unavailable():
    """知识库客户端缺失时明确报不可用。"""
    result = _manager(None).search_knowledge_for_agent("TP53")

    assert result["status"] == "error"
    assert result["outcome"] == "knowledge_unavailable"
    assert result["retrieval_error_reason"] == "client_unavailable"


def test_search_knowledge_retries_once_then_succeeds():
    """首次调用异常只重试一次；第二次成功即返回证据。"""
    client = FakeKnowledge(_retrieval(), error=RuntimeError("blip"), fail_times=1)

    result = _manager(client).search_knowledge_for_agent("TP53")

    assert len(client.calls) == 2
    assert result["status"] == "success"
    assert result["evidence_count"] == 1
    assert "outcome" not in result


def test_search_knowledge_survey_returns_diverse_bounded_sources():
    """survey 档返回 20 个来源、每源最多 2 条节选。"""
    chunks = []
    for index in range(1, 26):
        pid = f"4000{index}"
        for part in range(2):
            chunks.append(
                {
                    "chunk_id": f"chunk-{pid}-{part}",
                    "content": (f"标题：论文{index}\n摘要：{'证' * 2000}\n年份：2024\nPMID：{pid}"),
                }
            )
    result = _manager(FakeKnowledge(_retrieval(chunks=chunks))).search_knowledge_for_agent(
        "主题", scope="survey"
    )

    assert result["status"] == "success"
    assert result["scope"] == "survey"
    assert result["source_count"] == len(result["sources"]) == 20
    assert result["evidence_count"] == len(result["evidence"]) == 40
    assert all(len(item["content"]) <= 1200 for item in result["evidence"])


def test_search_knowledge_survey_caps_requested_sources_at_thirty():
    """survey 的 max_evidence 表示来源数，默认 20，请求超过 30 时截断。"""
    chunks = []
    for index in range(1, 36):
        pid = f"4100{index}"
        for part in range(2):
            chunks.append(
                {
                    "chunk_id": f"chunk-{pid}-{part}",
                    "content": f"标题：论文{index}\n摘要：证据\n年份：2024\nPMID：{pid}",
                }
            )
    manager = _manager(FakeKnowledge(_retrieval(chunks=chunks)))

    wide = manager.search_knowledge_for_agent("主题", scope="survey", max_evidence=30)
    capped = manager.search_knowledge_for_agent("主题", scope="survey", max_evidence=99)

    assert wide["source_count"] == len(wide["sources"]) == 30
    assert wide["evidence_count"] == 60
    assert capped["source_count"] == 30


def test_search_knowledge_exhaustive_pages_and_reports_coverage():
    """exhaustive 返回一页来源、has_more/next_cursor 与不承诺找全的覆盖说明。"""
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "content": f"标题：论文{index}\n摘要：摘要{index}\nPMID：{80000 + index}",
        }
        for index in range(1, 61)
    ]
    manager = _manager(FakeKnowledge(_retrieval(chunks=chunks)))

    first = manager.search_knowledge_for_agent("盘点", scope="exhaustive")
    second = manager.search_knowledge_for_agent(
        "盘点", scope="exhaustive", cursor=first["next_cursor"]
    )

    assert first["status"] == "success"
    assert first["scope"] == "exhaustive"
    assert first["source_count"] == len(first["sources"]) == 50
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert "不代表已找全" in first["coverage_message"]
    assert second["source_count"] == 10
    assert second["has_more"] is False
    assert second["next_cursor"] == ""


def test_search_knowledge_source_id_requires_exhaustive_scope():
    """按来源取正文时必须显式使用 exhaustive，避免被 answer 默认档静默忽略。"""
    result = _manager(FakeKnowledge(_retrieval())).search_knowledge_for_agent(
        "盘点", source_id="PMID 38947524"
    )

    assert result["status"] == "error"
    assert result["outcome"] == "invalid_scope_for_source_id"
    assert "scope=exhaustive" in result["message"]


def test_search_knowledge_paging_args_require_exhaustive_scope():
    """cursor 与 sources_only 不能被 answer 档静默忽略。"""
    manager = _manager(FakeKnowledge(_retrieval()))

    cursor_result = manager.search_knowledge_for_agent("TP53", cursor="abc")
    sources_result = manager.search_knowledge_for_agent("TP53", sources_only=True)

    assert cursor_result["outcome"] == "invalid_cursor"
    assert "scope=exhaustive" in cursor_result["message"]
    assert sources_result["outcome"] == "invalid_scope_for_sources_only"
    assert "scope=exhaustive" in sources_result["message"]


def test_search_knowledge_exhaustive_invalid_cursor_is_explicit():
    """查询变化后沿用旧游标返回可理解的 invalid_cursor，不伪装成服务故障。"""
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "content": f"标题：论文{index}\n摘要：摘要{index}\nPMID：{90000 + index}",
        }
        for index in range(1, 61)
    ]
    manager = _manager(FakeKnowledge(_retrieval(chunks=chunks)))
    first = manager.search_knowledge_for_agent("盘点", scope="exhaustive")

    bad = manager.search_knowledge_for_agent(
        "换了问题", scope="exhaustive", cursor=first["next_cursor"]
    )

    assert bad["status"] == "error"
    assert bad["outcome"] == "invalid_cursor"
    assert "游标无效" in bad["message"]


def test_search_knowledge_exhaustive_sources_only_is_not_insufficient():
    """sources_only 有来源但无证据时不算证据不足。"""
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "content": f"标题：论文{index}\n摘要：摘要{index}\nPMID：{91000 + index}",
        }
        for index in range(1, 61)
    ]
    result = _manager(FakeKnowledge(_retrieval(chunks=chunks))).search_knowledge_for_agent(
        "盘点", scope="exhaustive", sources_only=True
    )

    assert result["status"] == "success"
    assert result["evidence_count"] == 0
    assert result["source_count"] == 50
    assert "outcome" not in result
    assert "仅返回来源清单" in result["message"]


def test_search_knowledge_answer_defaults_to_eight_and_caps_at_twelve():
    """answer 默认 8 条、请求超限截到 12 条；每条正文保持完整。"""
    long_text = "证" * 3000
    chunks = [_chunk(pid=f"2000{index}").copy() for index in range(1, 15)]
    for index, chunk in enumerate(chunks, start=1):
        pid = f"2000{index}"
        chunk.update(
            {
                "chunk_id": f"chunk-{pid}",
                "content": f"标题：论文{index}\n摘要：{long_text}\nPMID：{pid}",
            }
        )
    manager = _manager(FakeKnowledge(_retrieval(chunks=chunks)))

    default = manager.search_knowledge_for_agent("TP53", scope="answer")
    capped = manager.search_knowledge_for_agent("TP53", scope="answer", max_evidence=99)

    assert len(default["evidence"]) == 8
    assert len(capped["evidence"]) == 12
    assert all(len(item["content"]) > 3000 for item in capped["evidence"])
    assert capped["source_count"] == 12


def test_search_knowledge_rejects_unknown_scope():
    """未支持的档位明确报错，不退化成 answer。"""
    result = _manager(FakeKnowledge(_retrieval())).search_knowledge_for_agent(
        "TP53", scope="compare"
    )

    assert result["status"] == "error"
    assert "不支持的检索范围" in result["message"]
