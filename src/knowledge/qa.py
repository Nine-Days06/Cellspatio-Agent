"""文献检索服务：把 LightRAG 检索结果映射为可追溯证据，不生成答案。

边界：只做检索、证据/来源装配与失败分类；回答由对话主模型基于证据自行生成，
因此本模块不再包含第二套问答协议。检索内容视为不可信数据。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from src.knowledge.async_runtime import runtime as async_runtime

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"^标题：(.*)$", re.MULTILINE)
_PMID_RE = re.compile(r"^PMID：(\d+)$", re.MULTILINE)
_DOI_RE = re.compile(r"^DOI：(10\.\d{4,9}/\S+)$", re.MULTILINE)
_YEAR_RE = re.compile(r"^年份：(\d{4})$", re.MULTILINE)

STAGE_RETRIEVAL = "retrieval"

# answer 档证据上限：默认 8 条，模型请求只能在上限内放大。
ANSWER_DEFAULT_EVIDENCE = 8
ANSWER_MAX_EVIDENCE = 12

# survey 档：按来源收敛，每源只留少量节选，避免把主题梳理变成全文堆叠。
SURVEY_DEFAULT_SOURCES = 20
SURVEY_MAX_SOURCES = 30
SURVEY_MAX_EVIDENCE_PER_SOURCE = 2
SURVEY_EVIDENCE_CHARS = 1200

# exhaustive 档：按来源分页，不返回 50 条全文；每页是一个有界窗口。
EXHAUSTIVE_PAGE_SOURCES = 50
EXHAUSTIVE_EVIDENCE_CHARS = 1200
EXHAUSTIVE_CURSOR_VERSION = 1
EXHAUSTIVE_COVERAGE_NOTE = (
    "这是当前知识库检索结果的有界一页，不代表已找全；需要更多来源时使用 next_cursor 继续。"
)
EXHAUSTIVE_LAST_PAGE_NOTE = "这是当前知识库检索结果的最后一页，不代表已找全。"


def evidence_limit(requested: Any, *, default: int, hard: int) -> int:
    """把模型请求的条数收敛到 [1, hard]；缺省或非法值用档位默认值。"""
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return default
    return min(max(value, 1), hard)


# 检索失败原因：供调用方区分“服务故障 / 超时 / 没有证据”，不靠中文文案反推。
RETRIEVAL_CLIENT_UNAVAILABLE = "client_unavailable"
RETRIEVAL_DEADLINE = "deadline_exceeded"
RETRIEVAL_CALL_FAILED = "call_failed"
RETRIEVAL_INVALID_RESULT = "invalid_result"
RETRIEVAL_INVALID_CURSOR = "invalid_cursor"
RETRIEVAL_SERVICE_FAILURE = "service_failure"

# 重排只在通过提供方显式启用时执行；默认零外部调用，失败只降级排序。
RERANK_DISABLED = "disabled"
RERANK_NOT_APPLICABLE = "not_applicable"
RERANK_APPLIED = "applied"
RERANK_PARTIAL = "partial"
RERANK_FALLBACK_TIMEOUT = "fallback_timeout"
RERANK_FALLBACK_ERROR = "fallback_error"
RERANK_FINGERPRINT_NONE = "none"

# 同一候选集只打分一次；分页复用顺序，避免每页重复请求重排服务。
RERANK_CACHE_LIMIT = 32
_rerank_cache: dict[str, tuple[list[str], str]] = {}

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
    pmid = pmid if pmid.isdigit() else ""
    doi = str(chunk.get("doi") or "").strip() or _first_match(_DOI_RE, content)
    if pmid:
        return {
            "source_id": f"PMID {pmid}",
            "pmid": pmid,
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    if doi:
        return {
            "source_id": f"DOI {doi}",
            "pmid": "",
            "doi": doi,
            "url": f"https://doi.org/{doi}",
        }
    return None


def _source_keys(identity: dict[str, str]) -> list[str]:
    """返回同一来源的候选身份键，用于 PMID/DOI/source 交叉去重。"""
    keys = [f"source:{identity['source_id']}"]
    if identity["pmid"]:
        keys.append(f"pmid:{identity['pmid']}")
    if identity["doi"]:
        keys.append(f"doi:{identity['doi'].lower()}")
    return keys


def _source_record_keys(source: dict[str, Any]) -> list[str]:
    """从已装配的来源记录还原去重键，供分页游标记录“已见来源”。"""
    keys = [f"source:{source['source_id']}"]
    if source.get("pmid"):
        keys.append(f"pmid:{source['pmid']}")
    if source.get("doi"):
        keys.append(f"doi:{str(source['doi']).lower()}")
    return keys


def _source_id_keys(source_id: str) -> list[str]:
    """把来源 ID 还原成可与游标 seen 比较的去重键。"""
    value = source_id.strip()
    if value.startswith("PMID "):
        pmid = value[5:].strip()
        if pmid:
            return [f"source:{value}", f"pmid:{pmid}"]
    elif value.startswith("DOI "):
        doi = value[4:].strip()
        if doi:
            return [f"source:{value}", f"doi:{doi.lower()}"]
    return [f"source:{value}"]


def _source_alias_index(
    chunks: list[Any],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """先扫一遍 chunk，把同一论文的 PMID 与 DOI 键并到同一来源。"""
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    parsed: list[tuple[list[str], dict[str, str]]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        raw = _chunk_text(chunk)
        if not raw:
            continue
        identity = _source_identity(chunk, raw)
        if identity is None:
            continue
        keys = _source_keys(identity)
        for key in keys:
            find(key)
        for key in keys[1:]:
            union(keys[0], key)
        parsed.append((keys, identity))

    canonical: dict[str, dict[str, str]] = {}
    for keys, identity in parsed:
        root = find(keys[0])
        current = canonical.get(root)
        # 同一论文既有 PMID 又有 DOI 时，优先用 PMID 作为稳定展示身份。
        if current is None or (identity["pmid"] and not current["pmid"]):
            canonical[root] = identity
    aliases: dict[str, str] = {}
    for keys, _ in parsed:
        root = find(keys[0])
        target = f"source:{canonical[root]['source_id']}"
        for key in keys:
            aliases[key] = target
    identities = {f"source:{identity['source_id']}": identity for identity in canonical.values()}
    return aliases, identities


def _query_fingerprint(question: str) -> str:
    """固定查询指纹，避免游标跨查询复用。"""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def _encode_cursor(
    question: str,
    offset: int,
    seen: set[str],
    next_evidence: int,
    rerank_fingerprint: str,
) -> str:
    """把分页状态压进不可读但自包含的游标。"""
    payload = {
        "v": EXHAUSTIVE_CURSOR_VERSION,
        "scope": "exhaustive",
        "q": _query_fingerprint(question),
        "rerank": rerank_fingerprint,
        "offset": offset,
        "seen": sorted(seen),
        "next_evidence": next_evidence,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw)).decode("ascii").rstrip("=")


def _decode_cursor(question: str, raw: str, rerank_fingerprint: str) -> dict[str, Any]:
    """解码并校验游标；不匹配当前查询时抛 ValueError。"""
    try:
        padding = "=" * (-len(raw) % 4)
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(raw + padding)))
    except Exception as exc:  # noqa: BLE001 - 任何损坏的游标都按无效处理
        raise ValueError("malformed cursor") from exc
    if not isinstance(data, dict) or data.get("v") != EXHAUSTIVE_CURSOR_VERSION:
        raise ValueError("unsupported cursor version")
    if data.get("scope") != "exhaustive" or data.get("q") != _query_fingerprint(question):
        raise ValueError("cursor does not match query or scope")
    # 旧游标没有指纹，按“重排关闭”继续可用。
    if data.get("rerank", RERANK_FINGERPRINT_NONE) != rerank_fingerprint:
        raise ValueError("cursor was created with a different reranker")
    seen = data.get("seen")
    if (
        not isinstance(data.get("offset"), int)
        or not isinstance(data.get("next_evidence"), int)
        or not isinstance(seen, list)
        or not all(isinstance(item, str) for item in seen)
    ):
        raise ValueError("malformed cursor payload")
    return data


def _build_evidence(
    chunks: list[Any],
    *,
    max_evidence: int,
    max_chars: int,
    max_sources: int | None = None,
    per_source_max: int | None = None,
    evidence_start: int = 1,
    skip_keys: set[str] | None = None,
    source_filter: str | None = None,
    sources_only: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """把 LightRAG chunks 转成证据与来源；形状异常或身份不可核对的片段计数后丢弃。"""
    aliases, canonical_identities = _source_alias_index(chunks)
    evidence: list[dict[str, Any]] = []
    sources: dict[str, dict[str, Any]] = {}
    key_to_source: dict[str, str] = {}
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
        keys = [aliases.get(key, key) for key in _source_keys(identity)]
        if skip_keys and any(key in skip_keys for key in keys):
            continue
        source_id = next(
            (key_to_source[key] for key in keys if key in key_to_source),
            keys[0].split(":", 1)[1],
        )
        if source_filter is not None and source_id != source_filter:
            continue
        identity = canonical_identities.get(keys[0], identity)
        source = sources.get(source_id)
        if source is None and max_sources is not None and len(sources) >= max_sources:
            break
        if (
            source is not None
            and per_source_max is not None
            and len(source["evidence_ids"]) >= per_source_max
        ):
            for key in keys:
                key_to_source.setdefault(key, source_id)
            continue
        # max_chars <= 0 表示保留完整正文，供 answer 档使用。
        content = raw if max_chars <= 0 else raw[:max_chars]
        chunk_id = str(chunk.get("chunk_id") or "")
        content_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        dedupe_key = chunk_id or content_sha256
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        file_path = str(chunk.get("file_path") or "")
        evidence_id = f"E{evidence_start + len(evidence)}"
        if sources_only:
            # sources_only 只返回来源清单，正文留到按 source_id 单独取。
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
                    "evidence_ids": [],
                }
            for key in keys:
                key_to_source.setdefault(key, source_id)
            if max_sources is not None and len(sources) >= max_sources:
                break
            continue
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
        for key in keys:
            key_to_source.setdefault(key, source_id)
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
    has_more: bool = False
    next_cursor: str = ""
    coverage_message: str = ""
    rerank_status: str = RERANK_DISABLED

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


class Reranker(Protocol):
    """提供方无关的重排器：分数与传入片段一一对应，无法打分用 None。"""

    fingerprint: str

    def score(self, query: str, passages: list[str]) -> Sequence[float | None]:
        """返回每个片段相对 query 的相关性分数。"""


def _chunk_cache_key(chunk: Any) -> str:
    """候选片段的稳定标识；空串表示无法安全缓存。"""
    if not isinstance(chunk, dict):
        return ""
    chunk_id = str(chunk.get("chunk_id") or "").strip()
    return chunk_id or hashlib.sha256(_chunk_text(chunk).encode("utf-8")).hexdigest()


def _rerank_cache_key(query: str, fingerprint: str, chunks: list[Any]) -> str:
    """按查询、重排指纹与候选集合定位一次重排结果。"""
    keys = sorted(_chunk_cache_key(chunk) for chunk in chunks)
    digest = hashlib.sha256("\x1f".join(keys).encode("utf-8")).hexdigest()
    return f"{_query_fingerprint(query)}:{fingerprint}:{digest}"


def _remember_rerank(cache_key: str, reranked: list[Any], status: str) -> None:
    """写入进程级重排缓存；失败状态也写，分页时不重复打故障服务。"""
    # shortcut: 无 TTL、不区分知识库与 endpoint，只有重启进程才失效；接入多库或多 endpoint 时再加作用域与过期。
    if not cache_key:
        return
    if len(_rerank_cache) >= RERANK_CACHE_LIMIT:
        _rerank_cache.pop(next(iter(_rerank_cache), ""), None)
    _rerank_cache[cache_key] = ([_chunk_cache_key(chunk) for chunk in reranked], status)


def _apply_rerank(
    query: str, chunks: list[Any], reranker: Reranker | None
) -> tuple[list[Any], str]:
    """按 query 相关性重排候选片段；只改顺序，失败回退原序。"""
    if reranker is None:
        return chunks, RERANK_DISABLED
    if not chunks:
        return chunks, RERANK_NOT_APPLICABLE
    chunk_keys = [_chunk_cache_key(chunk) for chunk in chunks]
    cacheable = all(chunk_keys) and len(set(chunk_keys)) == len(chunk_keys)
    cache_key = _rerank_cache_key(query, reranker.fingerprint, chunks) if cacheable else ""
    if cache_key:
        cached = _rerank_cache.get(cache_key)
        if cached is not None:
            order, cached_status = cached
            by_key = dict(zip(chunk_keys, chunks, strict=True))
            return [by_key[key] for key in order], cached_status
    passages = [_chunk_text(chunk) if isinstance(chunk, dict) else "" for chunk in chunks]
    try:
        raw_scores = list(reranker.score(query, passages))
    except TimeoutError:
        _remember_rerank(cache_key, chunks, RERANK_FALLBACK_TIMEOUT)
        return chunks, RERANK_FALLBACK_TIMEOUT
    except Exception:  # noqa: BLE001 - 外部服务任何异常都只降级排序，不中断检索
        _remember_rerank(cache_key, chunks, RERANK_FALLBACK_ERROR)
        return chunks, RERANK_FALLBACK_ERROR
    if len(raw_scores) > len(chunks):
        logger.warning("knowledge rerank returned extra scores: %s", len(raw_scores))
        _remember_rerank(cache_key, chunks, RERANK_FALLBACK_ERROR)
        return chunks, RERANK_FALLBACK_ERROR
    scores: list[float | None] = []
    for value in raw_scores:
        if value is None:
            scores.append(None)
        elif (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            logger.warning("knowledge rerank returned an invalid score")
            _remember_rerank(cache_key, chunks, RERANK_FALLBACK_ERROR)
            return chunks, RERANK_FALLBACK_ERROR
        else:
            scores.append(float(value))
    # 全部缺分（含响应为空）按规范回退原序，而不是报 partial。
    if not any(score is not None for score in scores):
        logger.warning(
            "knowledge rerank fallback status=%s reason=no_scores", RERANK_FALLBACK_ERROR
        )
        _remember_rerank(cache_key, chunks, RERANK_FALLBACK_ERROR)
        return chunks, RERANK_FALLBACK_ERROR
    # 缺分片段留在原位置，已打分片段按分数降序填入其余位置。
    scores.extend([None] * (len(chunks) - len(scores)))
    ordered = sorted(
        ((index, score) for index, score in enumerate(scores) if score is not None),
        key=lambda item: (-item[1], item[0]),
    )
    reranked: list[Any] = [None] * len(chunks)
    slots = [index for index, score in enumerate(scores) if score is not None]
    for slot, (index, _) in zip(slots, ordered, strict=True):
        reranked[slot] = chunks[index]
    for index, score in enumerate(scores):
        if score is None:
            reranked[index] = chunks[index]
    # 上限截断的尾部未参与打分属预期覆盖范围，不算 partial。
    status = RERANK_APPLIED if len(ordered) == len(raw_scores) else RERANK_PARTIAL
    _remember_rerank(cache_key, reranked, status)
    return reranked, status


def retrieve_evidence(
    knowledge_client: Any,
    question: str,
    *,
    deadline: float,
    on_stage: StageCallback | None = None,
    max_evidence: int | None = None,
    content_chars: int | None = None,
    max_sources: int | None = None,
    per_source_max: int | None = None,
    page_sources: int | None = None,
    scope: str = "answer",
    cursor: str | None = None,
    sources_only: bool = False,
    source_id: str | None = None,
    reranker: Reranker | None = None,
) -> EvidenceRetrieval:
    """检索一次文献并装配证据，不调用生成模型。

    只负责检索、映射与失败分类；是否把“没有证据”转成面向用户的结论由调用方
    决定。``deadline`` 为 ``time.monotonic()`` 基准的整轮截止时间；客户端在
    检索开始前断开时抛出 ``StageAborted``，调用方应转为中断终态。
    """
    limit_evidence = (
        max_evidence if max_evidence is not None else _env_int("KNOWLEDGE_QA_MAX_EVIDENCE", 8)
    )
    limit_chars = (
        content_chars
        if content_chars is not None
        else _env_int("KNOWLEDGE_QA_MAX_EVIDENCE_CHARS", 4000)
    )

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
        rerank_fingerprint = (
            reranker.fingerprint if reranker is not None else RERANK_FINGERPRINT_NONE
        )
        chunks, rerank_status = _apply_rerank(question, chunks, reranker)
        config["rerank_status"] = rerank_status
        if scope == "exhaustive":
            cursor_data: dict[str, Any] | None = None
            if cursor:
                try:
                    cursor_data = _decode_cursor(question, cursor, rerank_fingerprint)
                except ValueError:
                    return EvidenceRetrieval.failed(
                        "检索游标无效或与本次查询不匹配，请重新开始检索。",
                        reason=RETRIEVAL_INVALID_CURSOR,
                        config=config,
                    )
            skip_keys = set(cursor_data["seen"]) if cursor_data else set()
            next_evidence = int(cursor_data["next_evidence"]) if cursor_data else 1
            offset = int(cursor_data["offset"]) if cursor_data else 0
            if source_id:
                if cursor_data and not any(key in skip_keys for key in _source_id_keys(source_id)):
                    return EvidenceRetrieval.failed(
                        "指定来源不在当前查询的已见来源中，请重新获取来源清单。",
                        reason=RETRIEVAL_INVALID_CURSOR,
                        config=config,
                    )
                evidence, sources, untraceable = _build_evidence(
                    chunks,
                    max_evidence=max(len(chunks), 1),
                    max_chars=0,
                    max_sources=1,
                    evidence_start=next_evidence,
                    source_filter=source_id,
                )
                config["untraceable_chunks"] = untraceable
                return EvidenceRetrieval(
                    evidence=evidence,
                    sources=sources,
                    untraceable=untraceable,
                    config=config,
                    coverage_message=(
                        f"已返回指定来源的 {len(evidence)} 条正文片段。"
                        if evidence
                        else "未找到指定来源的正文片段。"
                    ),
                    rerank_status=rerank_status,
                )
            page_size = evidence_limit(
                page_sources, default=EXHAUSTIVE_PAGE_SOURCES, hard=EXHAUSTIVE_PAGE_SOURCES
            )
            evidence, sources, untraceable = _build_evidence(
                chunks,
                max_evidence=page_size + 1,
                max_chars=EXHAUSTIVE_EVIDENCE_CHARS,
                max_sources=page_size + 1,
                per_source_max=1,
                evidence_start=next_evidence,
                skip_keys=skip_keys,
                sources_only=sources_only,
            )
            has_more = len(sources) > page_size
            kept = sources[:page_size]
            if has_more:
                kept_ids = {source["source_id"] for source in kept}
                evidence = [item for item in evidence if item.get("source_id") in kept_ids]
            seen = set(skip_keys)
            for source in kept:
                seen.update(_source_record_keys(source))
            next_cursor = (
                _encode_cursor(
                    question,
                    offset + len(kept),
                    seen,
                    next_evidence + len(evidence),
                    rerank_fingerprint,
                )
                if has_more
                else ""
            )
            coverage = EXHAUSTIVE_COVERAGE_NOTE if has_more else EXHAUSTIVE_LAST_PAGE_NOTE
            if sources_only:
                coverage += " 本页仅返回来源清单。"
            config["untraceable_chunks"] = untraceable
            return EvidenceRetrieval(
                evidence=evidence,
                sources=kept,
                untraceable=untraceable,
                config=config,
                no_results=retrieval_status == "failure" and failure_reason == "no_results",
                has_more=has_more,
                next_cursor=next_cursor,
                coverage_message=coverage,
                rerank_status=rerank_status,
            )
        evidence, sources, untraceable = _build_evidence(
            chunks,
            max_evidence=limit_evidence,
            max_chars=limit_chars,
            max_sources=max_sources,
            per_source_max=per_source_max,
        )
        config["untraceable_chunks"] = untraceable
        return EvidenceRetrieval(
            evidence=evidence,
            sources=sources,
            untraceable=untraceable,
            config=config,
            no_results=retrieval_status == "failure" and failure_reason == "no_results",
            rerank_status=rerank_status,
        )
