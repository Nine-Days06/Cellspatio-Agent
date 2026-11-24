"""WorkflowManager 文献证据入口：只读检索、失败不冒充空结果。"""

from __future__ import annotations

from src.control.workflow_manager import WorkflowManager


class FakeKnowledge:
    """最小 LightRAG 客户端桩：回放既定结果或抛出既定异常。"""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def query_data(self, question, mode="mix", **kwargs):
        """记录调用参数；按构造时的 error/result 返回。"""
        if self.error:
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


def test_search_knowledge_returns_evidence_and_continues():
    """成功检索返回证据、来源与条数，并允许模型继续推理。"""
    result = _manager(FakeKnowledge(_retrieval())).search_knowledge_for_agent("TP53")

    assert result["status"] == "success"
    assert result["type"] == "knowledge_evidence"
    assert result["evidence_count"] == len(result["evidence"]) == 1
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
    """调用异常返回错误原因，不附带证据、不伪装成空检索。"""
    result = _manager(FakeKnowledge(error=RuntimeError("down"))).search_knowledge_for_agent("TP53")

    assert result["status"] == "error"
    assert result["retrieval_error_reason"] == "call_failed"
    assert "没有检索到" not in result["message"]
    assert "evidence" not in result


def test_search_knowledge_client_missing_is_unavailable():
    """知识库客户端缺失时明确报不可用。"""
    result = _manager(None).search_knowledge_for_agent("TP53")

    assert result["status"] == "error"
    assert result["retrieval_error_reason"] == "client_unavailable"
