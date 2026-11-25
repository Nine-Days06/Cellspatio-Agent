"""文献检索服务：把 LightRAG 检索结果映射为可追溯证据，不生成答案。

边界：只做检索、证据/来源装配与失败分类；回答由对话主模型基于证据自行生成，
因此本模块不再包含第二套问答协议。检索内容视为不可信数据。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from src.knowledge.async_runtime import runtime as async_runtime

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"^标题：(.*)$", re.MULTILINE)
_PMID_RE = re.compile(r"^PMID：(\d+)$", re.MULTILINE)
_DOI_RE = re.compile(r"^DOI：(10\.\d{4,9}/\S+)$", re.MULTILINE)
_YEAR_RE = re.compile(r"^年份：(\d{4})$", re.MULTILINE)

STAGE_RETRIEVAL = "retrieval"

# 检索失败原因：供调用方区分“服务故障 / 超时 / 没有证据”，不靠中文文案反推。
RETRIEVAL_CLIENT_UNAVAILABLE = "client_unavailable"
RETRIEVAL_DEADLINE = "deadline_exceeded"
RETRIEVAL_CALL_FAILED = "call_failed"
RETRIEVAL_INVALID_RESULT = "invalid_result"
RETRIEVAL_SERVICE_FAILURE = "service_failure"

# 阶段回调：(stage, phase) -> 是否继续；返回 False 表示客户端已断开。
StageCallback = Callable[[str, str], bool | None]


class StageAborted(Exception):
    """检索开始前发现客户端已断开；不发起检索。"""


@contextmanager
def _stage(on_stage: StageCallback | None, name: str) -> Iterator[None]:
    """上报检索阶段的开始与结束；断连或异常也会补发结束事件。"""
    if on_stage is not None and on_stage(name, "start") is False:
        on_stage(name, "end")
        raise StageAborted
    try:
        yield
    finally:
        if on_stage is not None:
            on_stage(name, "end")


def _env_int(name: str, default: int) -> int:
    """读取正整数环境变量，非法值回退默认值。"""
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw else default
    except ValueError:
        logger.warning("invalid %s=%r, using default %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("non-positive %s=%r, using default %s", name, raw, default)
        return default
    return value


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    """取首个正则分组，未命中返回空串。"""
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _chunk_text(chunk: dict[str, Any]) -> str:
    """取片段原文（不截断），哈希基于此值。"""
    return str(chunk.get("content") or "").strip()


def _source_identity(chunk: dict[str, Any], content: str) -> dict[str, str] | None:
    """只从 chunk 元数据或原文头解析可核对的身份，不做 basename 猜测。"""
    pmid = str(chunk.get("pmid") or "").strip() or _first_match(_PMID_RE, content)
    if pmid.isdigit():
        return {
            "source_id": f"PMID {pmid}",
            "pmid": pmid,
            "doi": "",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    doi = str(chunk.get("doi") or "").strip() or _first_match(_DOI_RE, content)
    if doi:
        return {
            "source_id": f"DOI {doi}",
            "pmid": "",
            "doi": doi,
            "url": f"https://doi.org/{doi}",
        }
    return None


def _build_evidence(
    chunks: list[Any],
    *,
    max_evidence: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """把 LightRAG chunks 转成证据与来源；形状异常或身份不可核对的片段计数后丢弃。"""
    evidence: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    untraceable = 0
    for chunk in chunks:
        # 检索结果是外部 JSON，元素类型不可信；非对象片段按不可核对处理
        if not isinstance(chunk, dict):
            untraceable += 1
            continue
        raw = _chunk_text(chunk)
        if not raw:
            continue
        identity = _source_identity(chunk, raw)
        if identity is None:
            untraceable += 1
            continue
        content = raw[:max_chars]
        chunk_id = str(chunk.get("chunk_id") or "")
        content_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        dedupe_key = chunk_id or content_sha256
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        file_path = str(chunk.get("file_path") or "")
        source_id = identity["source_id"]
        evidence_id = f"E{len(evidence) + 1}"
        evidence.append(
            {
                "evidence_id": evidence_id,
                "chunk_id": chunk_id,
                "reference_id": str(chunk.get("reference_id") or ""),
                "content": content,
                "content_sha256": content_sha256,
                "file_path": file_path,
                "source_id": source_id,
                "title": _first_match(_TITLE_RE, content),
                "year": _first_match(_YEAR_RE, content),
                "kind": "abstract" if "摘要：" in content else "document",
                "url": identity["url"],
            }
        )
        source = sources.get(source_id)
        if source is None:
            sources[source_id] = {
                "source_id": source_id,
                "pmid": identity["pmid"],
                "doi": identity["doi"],
                "title": _first_match(_TITLE_RE, content),
                "year": _first_match(_YEAR_RE, content),
                "species": str(chunk.get("species") or ""),
                "tissue": str(chunk.get("tissue") or ""),
                "technology": str(chunk.get("technology") or ""),
                "url": identity["url"],
                "evidence_ids": [evidence_id],
            }
        else:
            source["evidence_ids"].append(evidence_id)
        if len(evidence) >= max_evidence:
            break
    return evidence, list(sources.values()), untraceable


def _remaining(deadline: float) -> float:
    """返回本次检索可用秒数，超时抛 TimeoutError。"""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("knowledge retrieval deadline exceeded")
    return left


def _run_stage(coro: Any, deadline: float) -> Any:
    """在共享事件循环上执行一次检索协程，并受 deadline 约束。"""
    try:
        left = _remaining(deadline)
    except TimeoutError:
        # deadline 已过期时协程还没来得及被 await，显式关闭避免 RuntimeWarning
        if hasattr(coro, "close"):
            coro.close()
        raise
    return async_runtime.run(asyncio.wait_for(coro, timeout=left))


@dataclass(frozen=True)
class RetrievalError:
    """检索失败的最小事实：机器可读原因与用户可见文案。"""

    reason: str
    message: str


@dataclass(frozen=True)
class EvidenceRetrieval:
    """一次检索的只读结果，供知识问答与对话工具共用。

    ``error`` 只描述检索失败的最小事实；完整错误包由调用方用同一装配点生成，
    避免检索服务私自扩展公共响应形状。``config`` 只含本次检索相关字段。
    """

    evidence: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    untraceable: int
    config: dict[str, Any]
    no_results: bool = False
    error: RetrievalError | None = None

    @classmethod
    def failed(
        cls,
        message: str,
        *,
        reason: str,
        config: dict[str, Any] | None = None,
    ) -> EvidenceRetrieval:
        """构造不含证据的失败结果，统一空列表与计数字段。"""
        return cls(
            evidence=[],
            sources=[],
            untraceable=0,
            config=config if config is not None else {"retrieval_calls": 0},
            no_results=False,
            error=RetrievalError(reason=reason, message=message),
        )


def retrieve_evidence(
    knowledge_client: Any,
    question: str,
    *,
    deadline: float,
    on_stage: StageCallback | None = None,
) -> EvidenceRetrieval:
    """检索一次文献并装配证据，不调用生成模型。

    只负责检索、映射与失败分类；是否把“没有证据”转成面向用户的结论由调用方
    决定。``deadline`` 为 ``time.monotonic()`` 基准的整轮截止时间；客户端在
    检索开始前断开时抛出 ``StageAborted``，调用方应转为中断终态。
    """
    limit_evidence = _env_int("KNOWLEDGE_QA_MAX_EVIDENCE", 8)
    limit_chars = _env_int("KNOWLEDGE_QA_MAX_EVIDENCE_CHARS", 4000)

    if knowledge_client is None:
        return EvidenceRetrieval.failed(
            "知识库当前不可用，请稍后重试。",
            reason=RETRIEVAL_CLIENT_UNAVAILABLE,
        )
    # 计数只在真正发起检索时 +1；deadline 已过时不发出调用
    if deadline - time.monotonic() <= 0:
        return EvidenceRetrieval.failed(
            "处理超时，请稍后重试。",
            reason=RETRIEVAL_DEADLINE,
        )

    config: dict[str, Any] = {"retrieval_calls": 1}
    with _stage(on_stage, STAGE_RETRIEVAL):
        retrieval_started = time.monotonic()
        try:
            # 同步检索搬到线程里，受整轮 deadline 约束；超时后线程仍会跑完
            retrieval = _run_stage(
                asyncio.to_thread(knowledge_client.query_data, question, "mix", use_cache=False),
                deadline,
            )
        except TimeoutError:
            return EvidenceRetrieval.failed(
                "处理超时，请稍后重试。",
                reason=RETRIEVAL_DEADLINE,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - 统一收口为安全失败
            logger.warning("knowledge qa retrieval failed: %s", type(exc).__name__)
            return EvidenceRetrieval.failed(
                "知识检索失败，请稍后重试。",
                reason=RETRIEVAL_CALL_FAILED,
                config=config,
            )
        config["retrieval_ms"] = round((time.monotonic() - retrieval_started) * 1000)

        if not isinstance(retrieval, dict):
            return EvidenceRetrieval.failed(
                "知识检索返回了无法识别的结果。",
                reason=RETRIEVAL_INVALID_RESULT,
                config=config,
            )
        metadata = retrieval.get("metadata") if isinstance(retrieval.get("metadata"), dict) else {}
        retrieval_status = str(retrieval.get("status") or "")
        failure_reason = str(metadata.get("failure_reason") or "")
        config["retrieval_status"] = retrieval_status
        config["retrieval_metadata"] = metadata
        if retrieval_status not in ("success", "failure"):
            return EvidenceRetrieval.failed(
                "知识检索返回了无法识别的结果。",
                reason=RETRIEVAL_INVALID_RESULT,
                config=config,
            )
        # LightRAG 只有明确的 no_results 算“证据不足”，其余 failure 是依赖/配置故障
        if retrieval_status == "failure" and failure_reason != "no_results":
            return EvidenceRetrieval.failed(
                "知识检索失败，请稍后重试。",
                reason=RETRIEVAL_SERVICE_FAILURE,
                config=config,
            )
        data = retrieval.get("data")
        chunks = data.get("chunks") if isinstance(data, dict) else None
        # 成功响应必须是 {data: {chunks: [...]}}；形状异常归为依赖故障而非“无证据”
        if retrieval_status == "success" and (
            not isinstance(data, dict) or not isinstance(chunks, list)
        ):
            return EvidenceRetrieval.failed(
                "知识检索返回了无法识别的结果。",
                reason=RETRIEVAL_INVALID_RESULT,
                config=config,
            )
        chunks = chunks if isinstance(chunks, list) else []
        evidence, sources, untraceable = _build_evidence(
            chunks, max_evidence=limit_evidence, max_chars=limit_chars
        )
        config["untraceable_chunks"] = untraceable
        return EvidenceRetrieval(
            evidence=evidence,
            sources=sources,
            untraceable=untraceable,
            config=config,
            no_results=retrieval_status == "failure" and failure_reason == "no_results",
        )
