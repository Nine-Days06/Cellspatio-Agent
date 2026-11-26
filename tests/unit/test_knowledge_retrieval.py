"""文献检索服务：证据映射、失败分类与调用边界（不含任何生成协议）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import time
import zlib
from typing import Any

import pytest

from src.knowledge.qa import (
    ANSWER_DEFAULT_EVIDENCE,
    ANSWER_MAX_EVIDENCE,
    EXHAUSTIVE_CURSOR_VERSION,
    RERANK_APPLIED,
    RERANK_DISABLED,
    RERANK_FALLBACK_ERROR,
    RERANK_FALLBACK_TIMEOUT,
    RERANK_NOT_APPLICABLE,
    RERANK_PARTIAL,
    RETRIEVAL_CALL_FAILED,
    RETRIEVAL_CLIENT_UNAVAILABLE,
    RETRIEVAL_DEADLINE,
    RETRIEVAL_INVALID_RESULT,
    RETRIEVAL_SERVICE_FAILURE,
    SURVEY_EVIDENCE_CHARS,
    StageAborted,
    _query_fingerprint,
    evidence_limit,
    retrieve_evidence,
)

_FAKE_RERANK_SEQ = itertools.count()


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


class FakeReranker:
    """回放固定分数或异常，并记录收到的查询与片段。"""

    def __init__(
        self, scores: list[float | None] | None = None, error: BaseException | None = None
    ):
        self.scores = scores
        self.error = error
        self.fingerprint = f"fake:{next(_FAKE_RERANK_SEQ)}"
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages: list[str]) -> list[float | None]:
        """记录调用并回放构造时的分数或异常。"""
        self.calls.append((query, list(passages)))
        if self.error is not None:
            raise self.error
        return self.scores if self.scores is not None else []


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


def test_survey_limits_sources_and_excerpts_per_source() -> None:
    """survey 按来源收敛到 20 个，每源最多 2 条节选，PMID/DOI 交叉去重。"""
    chunks: list[dict[str, Any]] = []
    for index in range(1, 26):
        pid = f"3000{index}"
        for part in range(2):
            chunks.append(
                {
                    "chunk_id": f"chunk-{pid}-{part}",
                    "content": (f"标题：论文{index}\n摘要：{'证' * 2000}\n年份：2024\nPMID：{pid}"),
                }
            )
    chunks.append(
        {
            "chunk_id": "chunk-doi-duplicate",
            "content": "标题：论文1\n摘要：重复来源\nDOI：10.1000/duplicate",
        }
    )
    first = chunks[0]["content"]
    chunks[0]["content"] = f"{first}\nDOI：10.1000/duplicate"

    retrieval = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=chunks)),
        "主题",
        deadline=time.monotonic() + 5,
        max_evidence=60,
        max_sources=20,
        per_source_max=2,
        content_chars=SURVEY_EVIDENCE_CHARS,
    )

    assert len(retrieval.sources) == 20
    assert len(retrieval.evidence) == 40
    assert len({item["source_id"] for item in retrieval.evidence}) == 20
    assert all(len(item["content"]) <= SURVEY_EVIDENCE_CHARS for item in retrieval.evidence)
    assert len(retrieval.sources[0]["evidence_ids"]) == 2


def test_survey_merges_pmid_and_doi_seen_in_different_chunks() -> None:
    """同一论文的 PMID 与 DOI 分散在不同 chunk 时仍归并为一个来源。"""
    client = FakeKnowledge(
        _retrieval(
            chunks=[
                {
                    "chunk_id": "chunk-doi-first",
                    "content": "标题：同一论文\n摘要：第一段\nDOI：10.1000/shared",
                },
                {
                    "chunk_id": "chunk-pmid",
                    "content": ("标题：同一论文\n摘要：第二段\nPMID：12345\nDOI：10.1000/shared"),
                },
            ]
        )
    )

    retrieval = retrieve_evidence(
        client,
        "主题",
        deadline=time.monotonic() + 5,
        max_evidence=2,
        max_sources=20,
        per_source_max=2,
    )

    assert len(retrieval.sources) == 1
    assert retrieval.sources[0]["source_id"] == "PMID 12345"
    assert {item["source_id"] for item in retrieval.evidence} == {"PMID 12345"}


def test_exhaustive_pages_sources_and_keeps_ids_stable() -> None:
    """exhaustive 每页 50 个来源，跨页 evidence ID 连续且不重复。"""
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "content": f"标题：论文{index}\n摘要：摘要{index}\n年份：2024\nPMID：{50000 + index}",
        }
        for index in range(1, 121)
    ]
    client = FakeKnowledge(_retrieval(chunks=chunks))

    first = retrieve_evidence(client, "盘点", deadline=time.monotonic() + 5, scope="exhaustive")
    second = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        cursor=first.next_cursor,
    )
    third = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        cursor=second.next_cursor,
    )

    assert len(first.sources) == 50
    assert first.has_more is True and first.next_cursor
    assert [item["evidence_id"] for item in first.evidence][:1] == ["E1"]
    assert [item["evidence_id"] for item in second.evidence][:1] == ["E51"]
    assert [item["evidence_id"] for item in third.evidence][:1] == ["E101"]
    assert len(third.sources) == 20
    assert third.has_more is False and third.next_cursor == ""
    assert "最后一页" in third.coverage_message
    assert "next_cursor" not in third.coverage_message
    assert not (
        {item["source_id"] for item in first.evidence}
        & {item["source_id"] for item in second.evidence}
    )
    assert "不代表已找全" in first.coverage_message


def test_exhaustive_respects_requested_page_size() -> None:
    """exhaustive 的 max_evidence 请求缩小每页来源数，但不超过 50。"""
    chunks = [_chunk(pid=str(51000 + index)) for index in range(1, 26)]
    client = FakeKnowledge(_retrieval(chunks=chunks))

    small = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        page_sources=10,
    )
    capped = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        page_sources=99,
    )

    assert len(small.sources) == 10
    assert small.has_more is True
    assert len(capped.sources) == 25
    assert capped.has_more is False


def test_exhaustive_rejects_cursor_from_another_query() -> None:
    """游标绑定查询指纹，换查询后明确返回无效游标而不是错页。"""
    client = FakeKnowledge(
        _retrieval(chunks=[_chunk(pid=str(60000 + index)) for index in range(1, 61)])
    )
    first = retrieve_evidence(client, "盘点", deadline=time.monotonic() + 5, scope="exhaustive")

    bad = retrieve_evidence(
        client,
        "另一个问题",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        cursor=first.next_cursor,
    )

    assert bad.error is not None
    assert bad.error.reason == "invalid_cursor"
    assert "游标无效" in bad.error.message


def test_exhaustive_sources_only_then_fetches_one_source_text() -> None:
    """sources_only 只给来源清单；source_id 可取该来源完整正文。"""
    long_text = "证" * 5000
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "content": (f"标题：论文{index}\n摘要：{long_text}\n年份：2024\nPMID：{70000 + index}"),
        }
        for index in range(1, 61)
    ]
    chunks.extend(
        {
            "chunk_id": f"chunk-70001-extra-{index}",
            "content": f"标题：论文1\n摘要：{long_text}\n年份：2024\nPMID：70001",
        }
        for index in range(1, 13)
    )
    client = FakeKnowledge(_retrieval(chunks=chunks))

    page = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        sources_only=True,
    )
    source_id = page.sources[0]["source_id"]
    detail = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        cursor=page.next_cursor,
        source_id=source_id,
    )

    assert page.evidence == []
    assert len(page.sources) == 50
    assert page.has_more is True
    assert "仅返回来源清单" in page.coverage_message
    assert len(detail.evidence) == 13
    assert long_text in detail.evidence[0]["content"]
    assert detail.evidence[0]["source_id"] == source_id


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


def test_answer_scope_keeps_full_text_and_honors_hard_cap() -> None:
    """content_chars=0 保留完整正文；max_evidence 超过硬上限时截到 12 条。"""
    long_text = "证" * 5000
    chunks = [
        _chunk(pid=f"1000{index}", text=f"标题：论文{index}\n摘要：{long_text}\nPMID：1000{index}")
        for index in range(1, 15)
    ]

    retrieval = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=chunks)),
        "问题",
        deadline=time.monotonic() + 5,
        max_evidence=ANSWER_MAX_EVIDENCE,
        content_chars=0,
    )

    assert len(retrieval.evidence) == ANSWER_MAX_EVIDENCE
    assert all(long_text in item["content"] for item in retrieval.evidence)
    assert all("摘要：" in item["content"] for item in retrieval.evidence)


def test_evidence_limit_defaults_and_clamps() -> None:
    """缺省/非法请求用档位默认值，超限请求截到硬上限。"""
    assert evidence_limit(None, default=ANSWER_DEFAULT_EVIDENCE, hard=ANSWER_MAX_EVIDENCE) == 8
    assert evidence_limit("bad", default=8, hard=12) == 8
    assert evidence_limit(3, default=8, hard=12) == 3
    assert evidence_limit(99, default=8, hard=12) == 12


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


def test_rerank_is_disabled_without_a_provider() -> None:
    """默认没有重排器：不发起调用，顺序与状态都保持关闭。"""
    retrieval = retrieve_evidence(FakeKnowledge(), "问题", deadline=time.monotonic() + 5)

    assert retrieval.rerank_status == RERANK_DISABLED
    assert retrieval.config["rerank_status"] == RERANK_DISABLED
    assert [item["source_id"] for item in retrieval.evidence] == ["PMID 38947524"]


def test_rerank_reorders_by_score_without_renumbering() -> None:
    """假重排器改变候选顺序，证据编号在重排后才分配且保持连续。"""
    client = FakeKnowledge(_retrieval(chunks=[_chunk("111"), _chunk("222"), _chunk("333")]))
    reranker = FakeReranker(scores=[0.1, 0.9, 0.5])

    retrieval = retrieve_evidence(client, "问题", deadline=time.monotonic() + 5, reranker=reranker)

    assert retrieval.rerank_status == RERANK_APPLIED
    assert [item["source_id"] for item in retrieval.evidence] == [
        "PMID 222",
        "PMID 333",
        "PMID 111",
    ]
    assert [item["evidence_id"] for item in retrieval.evidence] == ["E1", "E2", "E3"]
    assert reranker.calls[0][0] == "问题"
    assert len(reranker.calls[0][1]) == 3


def test_rerank_equal_scores_keep_retrieval_order() -> None:
    """同分时按原检索顺序排列，结果稳定。"""
    client = FakeKnowledge(_retrieval(chunks=[_chunk("111"), _chunk("222"), _chunk("333")]))

    retrieval = retrieve_evidence(
        client,
        "问题",
        deadline=time.monotonic() + 5,
        reranker=FakeReranker(scores=[0.5, 0.5, 0.9]),
    )

    assert [item["source_id"] for item in retrieval.evidence] == [
        "PMID 333",
        "PMID 111",
        "PMID 222",
    ]


def test_rerank_skips_empty_candidates() -> None:
    """空候选集不调用重排器，报告 not_applicable 而非失败。"""
    reranker = FakeReranker(scores=[])

    retrieval = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=[])),
        "问题",
        deadline=time.monotonic() + 5,
        reranker=reranker,
    )

    assert retrieval.error is None
    assert retrieval.rerank_status == RERANK_NOT_APPLICABLE
    assert reranker.calls == []


def test_rerank_failures_fall_back_to_retrieval_order() -> None:
    """超时或异常只降级排序，不把检索变成失败。"""
    chunks = [_chunk("111"), _chunk("222")]

    timed_out = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=chunks)),
        "问题",
        deadline=time.monotonic() + 5,
        reranker=FakeReranker(error=TimeoutError()),
    )
    failed = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=chunks)),
        "问题",
        deadline=time.monotonic() + 5,
        reranker=FakeReranker(error=RuntimeError("boom")),
    )

    assert timed_out.error is None and timed_out.rerank_status == RERANK_FALLBACK_TIMEOUT
    assert failed.error is None and failed.rerank_status == RERANK_FALLBACK_ERROR
    assert [item["source_id"] for item in timed_out.evidence] == ["PMID 111", "PMID 222"]
    assert [item["source_id"] for item in failed.evidence] == ["PMID 111", "PMID 222"]


def test_rerank_partial_scores_keep_unscored_positions() -> None:
    """缺分片段留在原位置，已打分片段填满其余位置。"""
    client = FakeKnowledge(_retrieval(chunks=[_chunk("111"), _chunk("222"), _chunk("333")]))

    retrieval = retrieve_evidence(
        client,
        "问题",
        deadline=time.monotonic() + 5,
        reranker=FakeReranker(scores=[0.1, None, 0.9]),
    )

    assert retrieval.rerank_status == RERANK_PARTIAL
    assert [item["source_id"] for item in retrieval.evidence] == [
        "PMID 333",
        "PMID 222",
        "PMID 111",
    ]


def test_rerank_malformed_scores_fall_back() -> None:
    """非数值分数视为格式异常，回退原顺序。"""
    chunks = [_chunk("111"), _chunk("222")]

    retrieval = retrieve_evidence(
        FakeKnowledge(_retrieval(chunks=chunks)),
        "问题",
        deadline=time.monotonic() + 5,
        reranker=FakeReranker(scores=[0.5, "high"]),  # type: ignore[list-item]
    )

    assert retrieval.rerank_status == RERANK_FALLBACK_ERROR
    assert [item["source_id"] for item in retrieval.evidence] == ["PMID 111", "PMID 222"]


def test_exhaustive_rerank_runs_once_across_pages() -> None:
    """同一候选集只打分一次，后续页复用顺序且不重复来源。"""
    client = FakeKnowledge(_retrieval(chunks=[_chunk("111"), _chunk("222"), _chunk("333")]))
    reranker = FakeReranker(scores=[0.1, 0.9, 0.5])

    first = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        page_sources=1,
        reranker=reranker,
    )
    second = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        page_sources=1,
        cursor=first.next_cursor,
        reranker=reranker,
    )
    third = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        page_sources=1,
        cursor=second.next_cursor,
        reranker=reranker,
    )

    assert len(reranker.calls) == 1
    assert [page.sources[0]["source_id"] for page in (first, second, third)] == [
        "PMID 222",
        "PMID 333",
        "PMID 111",
    ]
    assert third.has_more is False


def test_exhaustive_cursor_rejects_changed_rerank_fingerprint() -> None:
    """换重排模型后旧游标被拒绝，而不是返回另一套顺序。"""
    client = FakeKnowledge(_retrieval(chunks=[_chunk(str(80000 + index)) for index in range(1, 4)]))
    first = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        page_sources=1,
        reranker=FakeReranker(scores=[0.9, 0.5, 0.1]),
    )

    bad = retrieve_evidence(
        client,
        "盘点",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        page_sources=1,
        cursor=first.next_cursor,
        reranker=FakeReranker(scores=[0.9, 0.5, 0.1]),
    )

    assert bad.error is not None
    assert bad.error.reason == "invalid_cursor"
    assert "游标无效" in bad.error.message


def test_exhaustive_accepts_legacy_cursor_without_rerank_fingerprint() -> None:
    """旧游标没有重排指纹时按“重排关闭”继续可用。"""
    payload = {
        "v": EXHAUSTIVE_CURSOR_VERSION,
        "scope": "exhaustive",
        "q": _query_fingerprint("问题"),
        "offset": 0,
        "seen": [],
        "next_evidence": 1,
    }
    raw = (
        base64.urlsafe_b64encode(
            zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        )
        .decode("ascii")
        .rstrip("=")
    )

    retrieval = retrieve_evidence(
        FakeKnowledge(),
        "问题",
        deadline=time.monotonic() + 5,
        scope="exhaustive",
        cursor=raw,
    )

    assert retrieval.error is None
    assert [item["evidence_id"] for item in retrieval.evidence] == ["E1"]
