"""文献检索服务：证据映射、失败分类与调用边界（不含任何生成协议）。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

import pytest

from src.knowledge.qa import (
    RETRIEVAL_CALL_FAILED,
    RETRIEVAL_CLIENT_UNAVAILABLE,
    RETRIEVAL_DEADLINE,
    RETRIEVAL_INVALID_RESULT,
    RETRIEVAL_SERVICE_FAILURE,
    StageAborted,
    retrieve_evidence,
)


class FakeKnowledge:
    """记录检索参数并回放 LightRAG query_data 形状的结果。"""

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.result = result if result is not None else _retrieval()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def query_data(self, question: str, mode: str = "mix", **kwargs: Any) -> Any:
        """记录调用参数；按构造时的 error/result 返回。"""
        self.calls.append({"question": question, "mode": mode, **kwargs})
        if self.error is not None:
            raise self.error
        return self.result


def _chunk(pid: str = "38947524", text: str | None = None) -> dict[str, Any]:
    """构造一条带可核对 PMID 的检索片段。"""
    return {
        "chunk_id": f"chunk-{pid}",
        "reference_id": "1",
        "file_path": pid,
        "content": text or f"标题：A paper\n摘要：Some evidence.\n年份：2024\nPMID：{pid}",
    }


def _retrieval(chunks: list[Any] | None = None) -> dict[str, Any]:
    """构造成功检索的 LightRAG 响应形状。"""
    return {
        "status": "success",
        "data": {"chunks": chunks if chunks is not None else [_chunk()]},
        "metadata": {"query_mode": "mix", "processing_info": {"total_chunks": 1}},
    }


def _stage_recorder(*, abort_on_start: bool = False) -> Any:
    """收集阶段事件；可让 start 事件返回 False 模拟客户端断开。"""
    events: list[tuple[str, str]] = []

    def on_stage(stage: str, phase: str) -> bool:
        events.append((stage, phase))
        return not (abort_on_start and phase == "start")

    on_stage.events = events  # type: ignore[attr-defined]
    return on_stage


def test_maps_chunks_without_calling_a_model() -> None:
    """检索服务只做检索与证据装配，不产出答案。"""
    client = FakeKnowledge()

    retrieval = retrieve_evidence(client, "问题", deadline=time.monotonic() + 5)

    assert retrieval.error is None
    assert [item["evidence_id"] for item in retrieval.evidence] == ["E1"]
    assert retrieval.evidence[0]["source_id"] == "PMID 38947524"
    assert retrieval.evidence[0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/38947524/"
    assert retrieval.evidence[0]["title"] == "A paper"
    assert retrieval.sources[0]["pmid"] == "38947524"
    assert retrieval.untraceable == 0
    assert retrieval.config["retrieval_calls"] == 1
    assert retrieval.config["retrieval_status"] == "success"
    assert client.calls == [{"question": "问题", "mode": "mix", "use_cache": False}]


def test_untraceable_and_non_dict_chunks_are_counted_not_crashing() -> None:
    """外部 JSON 形状不可信：不可核对片段计数后丢弃，不整轮报错。"""
    client = FakeKnowledge(
        _retrieval(
            chunks=[
                "oops",
                {
                    "chunk_id": "c-unknown",
                    "file_path": "unknown_source",
                    "content": "标题：来路不明的片段\n摘要：无身份信息。",
                },
                _chunk(),
            ]
        )
    )

    retrieval = retrieve_evidence(client, "问题", deadline=time.monotonic() + 5)

    assert retrieval.error is None
    assert [item["evidence_id"] for item in retrieval.evidence] == ["E1"]
    assert retrieval.untraceable == 2
    assert retrieval.config["untraceable_chunks"] == 2


def test_duplicate_chunks_are_collapsed_before_numbering() -> None:
    """同一 chunk 重复返回时只保留一条，编号保持连续。"""
    client = FakeKnowledge(_retrieval(chunks=[_chunk(), _chunk()]))

    retrieval = retrieve_evidence(client, "问题", deadline=time.monotonic() + 5)

    assert [item["evidence_id"] for item in retrieval.evidence] == ["E1"]
    assert retrieval.sources[0]["evidence_ids"] == ["E1"]


def test_past_deadline_skips_the_call() -> None:
    """截止时间已过时不发起检索，计数保持 0。"""
    client = FakeKnowledge()

    retrieval = retrieve_evidence(client, "问题", deadline=time.monotonic() - 1)

    assert retrieval.error is not None
    assert retrieval.error.reason == RETRIEVAL_DEADLINE
    assert "超时" in retrieval.error.message
    assert retrieval.config["retrieval_calls"] == 0
    assert client.calls == []


def test_mid_call_timeout_is_reported_as_deadline() -> None:
    """检索已发出后超时属于超时，不能被归并成普通检索失败。"""

    class SlowKnowledge(FakeKnowledge):
        def query_data(self, question: str, mode: str = "mix", **kwargs: Any) -> Any:
            time.sleep(0.2)
            return super().query_data(question, mode, **kwargs)

    retrieval = retrieve_evidence(SlowKnowledge(), "问题", deadline=time.monotonic() + 0.01)

    assert retrieval.error is not None
    assert retrieval.error.reason == RETRIEVAL_DEADLINE
    assert retrieval.config["retrieval_calls"] == 1


def test_missing_client_is_unavailable_without_counting_a_call() -> None:
    """知识库客户端缺失属于依赖不可用，不记为一次检索。"""
    retrieval = retrieve_evidence(None, "问题", deadline=time.monotonic() + 5)

    assert retrieval.error is not None
    assert retrieval.error.reason == RETRIEVAL_CLIENT_UNAVAILABLE
    assert "不可用" in retrieval.error.message
    assert retrieval.config["retrieval_calls"] == 0


def test_call_exception_is_safe_and_does_not_leak_details() -> None:
    """底层异常文案可能含凭据，对外只给固定安全文案。"""
    retrieval = retrieve_evidence(
        FakeKnowledge(error=RuntimeError("postgres password leaked")),
        "问题",
        deadline=time.monotonic() + 5,
    )

    assert retrieval.error is not None
    assert retrieval.error.reason == RETRIEVAL_CALL_FAILED
    assert "password" not in retrieval.error.message
    assert retrieval.config["retrieval_calls"] == 1


@pytest.mark.parametrize(
    ("failure_reason", "is_error"),
    [("no_results", False), ("embedding_unavailable", True)],
)
def test_only_no_results_counts_as_empty_retrieval(failure_reason: str, is_error: bool) -> None:
    """只有 no_results 是“没有证据”，其余 failure 是服务故障。"""
    client = FakeKnowledge(
        {
            "status": "failure",
            "data": {},
            "metadata": {"failure_reason": failure_reason, "mode": "mix"},
        }
    )

    retrieval = retrieve_evidence(client, "问题", deadline=time.monotonic() + 5)

    assert (retrieval.error is not None) is is_error
    assert retrieval.no_results is (not is_error)
    if is_error:
        assert retrieval.error.reason == RETRIEVAL_SERVICE_FAILURE
    assert retrieval.evidence == []
    assert retrieval.config["retrieval_status"] == "failure"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "success", "data": "bad"},
        {"status": "success", "data": {"chunks": "bad"}},
        {"status": "success", "metadata": {}},
        {"status": "", "data": {"chunks": []}},
        {},
    ],
)
def test_malformed_payload_is_an_error_not_empty_evidence(payload: dict[str, Any]) -> None:
    """成功响应形状不对是依赖故障，不能伪装成“检索成功但无证据”。"""
    retrieval = retrieve_evidence(FakeKnowledge(payload), "问题", deadline=time.monotonic() + 5)

    assert retrieval.error is not None
    assert retrieval.error.reason == RETRIEVAL_INVALID_RESULT
    assert retrieval.evidence == []
    assert retrieval.config["retrieval_calls"] == 1


def test_doi_identity_is_used_when_pmid_absent() -> None:
    """无 PMID 时用 DOI 建立可核对来源。"""
    text = "标题：DOI paper\nDOI：10.1038/s41586-024-00001-2\n摘要：Evidence."
    retrieval = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=[{"chunk_id": "c1", "content": text}])),
        "问题",
        deadline=time.monotonic() + 5,
    )

    assert retrieval.evidence[0]["source_id"] == "DOI 10.1038/s41586-024-00001-2"
    assert retrieval.evidence[0]["url"] == "https://doi.org/10.1038/s41586-024-00001-2"
    assert retrieval.sources[0]["doi"] == "10.1038/s41586-024-00001-2"
    assert retrieval.sources[0]["pmid"] == ""


def test_source_background_fields_come_from_chunk_metadata() -> None:
    """物种/组织/技术只能来自 chunk 元数据，不做推断。"""
    chunk = _chunk()
    chunk.update({"species": "human", "tissue": "brain", "technology": "10x Visium"})

    retrieval = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=[chunk])), "问题", deadline=time.monotonic() + 5
    )

    source = retrieval.sources[0]
    assert (source["species"], source["tissue"], source["technology"]) == (
        "human",
        "brain",
        "10x Visium",
    )


def test_content_hash_covers_full_text_not_truncated() -> None:
    """正文按上限截断，但哈希必须覆盖完整原文以便核对。"""
    long_text = "标题：长文\nPMID：38947524\n摘要：" + "证据" * 5000

    retrieval = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=[{"chunk_id": "c1", "content": long_text}])),
        "问题",
        deadline=time.monotonic() + 5,
    )

    assert len(retrieval.evidence[0]["content"]) < len(long_text)
    assert (
        retrieval.evidence[0]["content_sha256"]
        == hashlib.sha256(long_text.encode("utf-8")).hexdigest()
    )


def test_retrieval_runs_off_loop_with_real_runtime_nesting() -> None:
    """真实 query_data 被搬到线程后仍能走共享运行时，不得自锁。"""
    from src.knowledge.async_runtime import runtime

    class NestedClient:
        def query_data(self, question: str, mode: str = "mix", **kwargs: Any) -> Any:
            async def inner() -> dict[str, Any]:
                await asyncio.sleep(0.01)
                return _retrieval()

            return runtime.run(inner())

    retrieval = retrieve_evidence(NestedClient(), "问题", deadline=time.monotonic() + 5)

    assert retrieval.error is None
    assert retrieval.config["retrieval_calls"] == 1


def test_stage_callback_reports_a_single_paired_retrieval_stage() -> None:
    """检索只有一对阶段事件；结束后不再有生成阶段。"""
    on_stage = _stage_recorder()

    retrieve_evidence(FakeKnowledge(), "问题", deadline=time.monotonic() + 5, on_stage=on_stage)

    assert on_stage.events == [("retrieval", "start"), ("retrieval", "end")]


def test_stage_callback_aborts_before_the_call_when_client_is_gone() -> None:
    """开始事件发不出去说明连接已断，此时不得发起检索。"""
    on_stage = _stage_recorder(abort_on_start=True)
    client = FakeKnowledge()

    with pytest.raises(StageAborted):
        retrieve_evidence(client, "问题", deadline=time.monotonic() + 5, on_stage=on_stage)

    assert client.calls == []
    assert on_stage.events == [("retrieval", "start"), ("retrieval", "end")]


def test_stage_callback_is_optional() -> None:
    """不传回调时结果一致。"""
    retrieval = retrieve_evidence(FakeKnowledge(), "问题", deadline=time.monotonic() + 5)

    assert retrieval.error is None
    assert [item["evidence_id"] for item in retrieval.evidence] == ["E1"]
