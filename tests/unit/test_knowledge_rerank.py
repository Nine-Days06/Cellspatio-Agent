"""文献重排适配器：配置、分批打分与检索层联动。"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from src.knowledge import rerank as rerank_module
from src.knowledge.qa import (
    RERANK_APPLIED,
    RERANK_FALLBACK_ERROR,
    RERANK_FALLBACK_TIMEOUT,
    retrieve_evidence,
)
from src.knowledge.rerank import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_CANDIDATES,
    HttpReranker,
    RerankConfig,
    build_reranker,
    rerank_config_from_env,
)


class FakeKnowledge:
    """最小 LightRAG 客户端桩：回放既定 chunks。"""

    def __init__(self, chunks: list[Any]) -> None:
        self.result = {"status": "success", "data": {"chunks": chunks}, "metadata": {}}

    def query_data(self, question: str, mode: str = "mix", **kwargs: Any) -> Any:
        """忽略参数，返回构造时的结果。"""
        return self.result


def _chunk(pid: str) -> dict[str, Any]:
    """构造一条带可核对 PMID 的检索片段。"""
    return {
        "chunk_id": f"chunk-{pid}",
        "file_path": pid,
        "content": f"标题：Paper {pid}\n摘要：Evidence.\n年份：2024\nPMID：{pid}",
    }


def _config(**overrides: Any) -> RerankConfig:
    """构造启用状态的 HTTP 配置。"""
    values = {
        "provider": "http",
        "endpoint": "http://127.0.0.1:8080/rerank",
        "model": "bge-reranker",
        "timeout": 5.0,
        "batch_size": 2,
        "max_candidates": 4,
    }
    values.update(overrides)
    return RerankConfig(**values)


def test_rerank_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有显式配置时不构造重排器。"""
    monkeypatch.delenv("KNOWLEDGE_RERANK_PROVIDER", raising=False)
    monkeypatch.delenv("KNOWLEDGE_RERANK_ENDPOINT", raising=False)

    config = rerank_config_from_env()

    assert config.provider == "none"
    assert config.fingerprint == "none"
    assert build_reranker(config) is None


def test_rerank_config_reads_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 provider + endpoint 才启用，其余字段有默认值。"""
    monkeypatch.setenv("KNOWLEDGE_RERANK_PROVIDER", "HTTP")
    monkeypatch.setenv("KNOWLEDGE_RERANK_ENDPOINT", "http://127.0.0.1:8080/rerank")
    monkeypatch.setenv("KNOWLEDGE_RERANK_MODEL", "bge-reranker")

    config = rerank_config_from_env()

    assert config.enabled is True
    assert config.fingerprint == "http:bge-reranker"
    assert config.batch_size == DEFAULT_BATCH_SIZE
    assert config.max_candidates == DEFAULT_MAX_CANDIDATES
    assert isinstance(build_reranker(config), HttpReranker)


@pytest.mark.parametrize(
    ("provider", "endpoint"),
    [("other", "http://127.0.0.1:8080/rerank"), ("http", "127.0.0.1:8080/rerank")],
)
def test_invalid_rerank_config_stays_disabled(
    monkeypatch: pytest.MonkeyPatch, provider: str, endpoint: str
) -> None:
    """未知提供方或非 http(s) endpoint 一律关闭，不发请求。"""
    monkeypatch.setenv("KNOWLEDGE_RERANK_PROVIDER", provider)
    monkeypatch.setenv("KNOWLEDGE_RERANK_ENDPOINT", endpoint)

    assert rerank_config_from_env().provider == "none"


def test_rerank_batches_and_caps_candidates() -> None:
    """按批发送，候选数受上限约束，分数按索引对齐。"""
    calls: list[dict[str, Any]] = []

    def post(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        calls.append({"url": url, "payload": payload, "timeout": timeout})
        return [{"index": index, "score": 1.0} for index in range(len(payload["texts"]))]

    reranker = HttpReranker(_config(), post=post)

    scores = reranker.score("问题", [f"p{index}" for index in range(5)])

    assert scores == [1.0, 1.0, 1.0, 1.0]
    assert [call["payload"]["texts"] for call in calls] == [["p0", "p1"], ["p2", "p3"]]
    assert calls[0]["payload"]["query"] == "问题"
    assert calls[0]["payload"]["model"] == "bge-reranker"


def test_rerank_aligns_scores_by_index_and_keeps_missing_entries() -> None:
    """响应乱序时按 index 还原；缺分保留 None 由检索层标记 partial。"""

    def post(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        return [{"index": 0, "score": 0.25}]

    reranker = HttpReranker(_config(batch_size=2), post=post)

    assert reranker.score("问题", ["a", "b"]) == [0.25, None]


@pytest.mark.parametrize(
    "response",
    [
        {"results": []},
        ["not-an-object"],
        [{"index": 5, "score": 1.0}],
        [{"index": 0, "score": "high"}],
    ],
)
def test_rerank_malformed_response_raises(response: Any) -> None:
    """格式、索引或分数非法时抛错，由检索层回退原序。"""
    reranker = HttpReranker(_config(), post=lambda url, payload, timeout: response)

    with pytest.raises(ValueError):
        reranker.score("问题", ["a", "b"])


def test_retrieve_evidence_applies_http_rerank() -> None:
    """适配器接入检索层后改变候选顺序，并报告 applied。"""

    def post(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        return [{"index": 0, "score": 0.1}, {"index": 1, "score": 0.9}]

    retrieval = retrieve_evidence(
        FakeKnowledge([_chunk("111"), _chunk("222")]),
        "问题",
        deadline=time.monotonic() + 5,
        reranker=HttpReranker(_config(), post=post),
    )

    assert retrieval.error is None
    assert retrieval.rerank_status == RERANK_APPLIED
    assert [item["source_id"] for item in retrieval.evidence] == ["PMID 222", "PMID 111"]


def test_retrieve_evidence_timeout_falls_back() -> None:
    """HTTP 超时只降级排序，检索仍然成功。"""

    def post(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        raise TimeoutError("slow")

    retrieval = retrieve_evidence(
        FakeKnowledge([_chunk("111"), _chunk("222")]),
        "超时问题",
        deadline=time.monotonic() + 5,
        reranker=HttpReranker(_config(), post=post),
    )

    assert retrieval.error is None
    assert retrieval.rerank_status == RERANK_FALLBACK_TIMEOUT
    assert [item["source_id"] for item in retrieval.evidence] == ["PMID 111", "PMID 222"]


def test_rerank_cap_truncation_is_applied_not_partial() -> None:
    """候选超过上限时只截断尾部，截断内的集合完整打分仍算 applied。"""

    def post(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        return [
            {"index": index, "score": float(text.split("Paper ")[1].split("\n")[0])}
            for index, text in enumerate(payload["texts"])
        ]

    retrieval = retrieve_evidence(
        FakeKnowledge([_chunk(str(index)) for index in range(6)]),
        "上限问题",
        deadline=time.monotonic() + 5,
        reranker=HttpReranker(_config(max_candidates=4), post=post),
    )

    assert retrieval.rerank_status == RERANK_APPLIED
    assert [item["source_id"] for item in retrieval.evidence] == [
        "PMID 3",
        "PMID 2",
        "PMID 1",
        "PMID 0",
        "PMID 4",
        "PMID 5",
    ]


def test_rerank_all_scores_missing_falls_back() -> None:
    """空响应（全部缺分）按规范回退原序，而不是报 partial。"""

    def post(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        return []

    retrieval = retrieve_evidence(
        FakeKnowledge([_chunk("111"), _chunk("222")]),
        "全缺分问题",
        deadline=time.monotonic() + 5,
        reranker=HttpReranker(_config(), post=post),
    )

    assert retrieval.rerank_status == RERANK_FALLBACK_ERROR
    assert [item["source_id"] for item in retrieval.evidence] == ["PMID 111", "PMID 222"]


def test_rerank_fallback_is_cached_across_pages() -> None:
    """同一候选集分页只打一次故障服务，避免每页重复等超时。"""
    calls: list[int] = []

    def post(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        calls.append(len(payload["texts"]))
        raise TimeoutError("slow")

    chunks = [_chunk("111"), _chunk("222"), _chunk("333")]
    reranker = HttpReranker(_config(), post=post)
    for _ in range(2):
        retrieval = retrieve_evidence(
            FakeKnowledge(chunks),
            "回退缓存问题",
            deadline=time.monotonic() + 5,
            scope="exhaustive",
            page_sources=1,
            reranker=reranker,
        )
        assert retrieval.rerank_status == RERANK_FALLBACK_TIMEOUT

    assert len(calls) == 1


def _fake_client(post_impl: Any) -> type:
    """构造 httpx.Client 替身，走真实 ``_http_post`` 分支。"""

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

        def post(self, url: str, json: dict[str, Any]) -> Any:
            return post_impl(url)

    return FakeClient


def test_http_post_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx 超时统一转 TimeoutError，检索层据此标记 fallback_timeout。"""

    def post(url: str) -> Any:
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(rerank_module.httpx, "Client", _fake_client(post))
    with pytest.raises(TimeoutError):
        HttpReranker(_config()).score("问题", ["a"])


def test_http_rerank_non_2xx_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 2xx 响应走真实 raise_for_status，检索回退且不失败。"""

    def post(url: str) -> Any:
        return httpx.Response(500, request=httpx.Request("POST", url))

    monkeypatch.setattr(rerank_module.httpx, "Client", _fake_client(post))
    retrieval = retrieve_evidence(
        FakeKnowledge([_chunk("111"), _chunk("222")]),
        "非2xx问题",
        deadline=time.monotonic() + 5,
        reranker=HttpReranker(_config()),
    )

    assert retrieval.error is None
    assert retrieval.rerank_status == RERANK_FALLBACK_ERROR


def test_http_post_raises_on_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """连接失败按传输错误上抛，检索层回退而不是中断。"""

    def post(url: str) -> Any:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(rerank_module.httpx, "Client", _fake_client(post))
    with pytest.raises(httpx.ConnectError):
        HttpReranker(_config()).score("问题", ["a"])


def test_http_post_raises_on_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """响应不是 JSON 时抛 ValueError，由检索层回退。"""

    class _BadJson:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            raise ValueError("bad json")

    monkeypatch.setattr(rerank_module.httpx, "Client", _fake_client(lambda url: _BadJson()))
    with pytest.raises(ValueError):
        HttpReranker(_config()).score("问题", ["a"])
