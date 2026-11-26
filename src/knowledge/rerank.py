"""文献重排适配器：TEI 兼容 HTTP 打分，失败只降级排序。

默认关闭；只有显式配置 ``KNOWLEDGE_RERANK_PROVIDER=http`` 与 endpoint 时才发送
查询和片段文本，属于运维的数据出境决策。
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import env_float, env_int

logger = logging.getLogger(__name__)

PROVIDER_NONE = "none"
PROVIDER_HTTP = "http"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_BATCH_SIZE = 16
DEFAULT_MAX_CANDIDATES = 120

# 注入点：测试用假传输替代真实 HTTP，不 mock 内部实现。
Post = Callable[[str, dict[str, Any], float], Any]


@dataclass(frozen=True)
class RerankConfig:
    """一次部署的重排配置；默认关闭。"""

    provider: str = PROVIDER_NONE
    endpoint: str = ""
    model: str = ""
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    batch_size: int = DEFAULT_BATCH_SIZE
    max_candidates: int = DEFAULT_MAX_CANDIDATES

    @property
    def enabled(self) -> bool:
        """只有显式 http 提供方才算启用。"""
        return self.provider == PROVIDER_HTTP

    @property
    def fingerprint(self) -> str:
        """提供方 + 模型；用于拒绝配置变化后的旧分页游标。"""
        return f"{self.provider}:{self.model}" if self.enabled else PROVIDER_NONE


def rerank_config_from_env() -> RerankConfig:
    """从 ``KNOWLEDGE_RERANK_*`` 读取配置；缺 endpoint 或提供方非法时关闭。"""
    provider = (os.environ.get("KNOWLEDGE_RERANK_PROVIDER") or PROVIDER_NONE).strip().lower()
    endpoint = (os.environ.get("KNOWLEDGE_RERANK_ENDPOINT") or "").strip()
    model = (os.environ.get("KNOWLEDGE_RERANK_MODEL") or "").strip()
    config = RerankConfig(
        provider=provider,
        endpoint=endpoint,
        model=model,
        timeout=env_float("KNOWLEDGE_RERANK_TIMEOUT", DEFAULT_TIMEOUT_SECONDS),
        batch_size=env_int("KNOWLEDGE_RERANK_BATCH_SIZE", DEFAULT_BATCH_SIZE),
        max_candidates=env_int("KNOWLEDGE_RERANK_MAX_CANDIDATES", DEFAULT_MAX_CANDIDATES),
    )
    if provider == PROVIDER_NONE:
        return config
    if provider != PROVIDER_HTTP:
        logger.warning("unsupported KNOWLEDGE_RERANK_PROVIDER=%r, rerank disabled", provider)
        return RerankConfig()
    if not endpoint.startswith(("http://", "https://")):
        logger.warning("KNOWLEDGE_RERANK_ENDPOINT missing or not http(s), rerank disabled")
        return RerankConfig()
    return config


def build_reranker(
    config: RerankConfig | None = None, *, post: Post | None = None
) -> HttpReranker | None:
    """按配置构造重排器；未启用时返回 ``None``（检索层据此零调用）。"""
    config = config if config is not None else rerank_config_from_env()
    if not config.enabled:
        return None
    return HttpReranker(config, post=post)


class HttpReranker:
    """TEI 兼容 ``/rerank`` 客户端：分批打分，返回与片段对齐的分数。"""

    def __init__(self, config: RerankConfig, *, post: Post | None = None) -> None:
        self.config = config
        self._post = post or _http_post

    @property
    def fingerprint(self) -> str:
        """提供方 + 模型；分页游标据此拒绝配置已变化的旧页。"""
        return self.config.fingerprint

    def score(self, query: str, passages: list[str]) -> Sequence[float | None]:
        """只打分前 ``max_candidates`` 条；总超时耗尽时抛 ``TimeoutError`` 整轮降级。"""
        targets = passages[: self.config.max_candidates]
        scores: list[float | None] = []
        started = time.monotonic()
        deadline = started + self.config.timeout
        try:
            for start in range(0, len(targets), self.config.batch_size):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("rerank budget exhausted")
                batch = targets[start : start + self.config.batch_size]
                scores.extend(self._score_batch(query, batch, min(self.config.timeout, remaining)))
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "knowledge rerank failed provider=http model=%s candidates=%d latency_ms=%d error=%s",
                self.config.model or "-",
                len(targets),
                round((time.monotonic() - started) * 1000),
                type(exc).__name__,
            )
            raise
        logger.info(
            "knowledge rerank provider=http model=%s candidates=%d scored=%d latency_ms=%d",
            self.config.model or "-",
            len(targets),
            sum(1 for score in scores if score is not None),
            round((time.monotonic() - started) * 1000),
        )
        return scores

    def _score_batch(self, query: str, batch: list[str], timeout: float) -> list[float | None]:
        """调用一次 HTTP 打分，把 ``[{index, score}]`` 还原为对齐列表。"""
        payload = {"query": query, "texts": batch, "model": self.config.model}
        data = self._post(self.config.endpoint, payload, timeout)
        if not isinstance(data, list):
            raise ValueError("rerank response is not a list")
        scores: list[float | None] = [None] * len(batch)
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("rerank response item is not an object")
            index, score = item.get("index"), item.get("score")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(batch):
                raise ValueError("rerank response index out of range")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise ValueError("rerank response score is not a number")
            scores[index] = float(score)
        return scores


def _http_post(url: str, payload: dict[str, Any], timeout: float) -> Any:
    """最小 TEI 客户端；超时统一转成 ``TimeoutError`` 供检索层分类。"""
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError("rerank request timed out") from exc
