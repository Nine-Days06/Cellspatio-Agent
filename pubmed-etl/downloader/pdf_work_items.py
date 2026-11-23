"""OA 全文工作项的执行、分类与恢复身份。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import LOG_DIR
from utils.logger import get_logger

logger = get_logger("pdf_work_items", log_dir=LOG_DIR)

_MAX_WORKERS = 8
_PROGRESS_INTERVAL = 10


@dataclass(frozen=True, slots=True)
class PdfWorkItem:
    """可持久化、可重新查询链接的一篇 OA 全文工作项。"""

    pmid: str
    pmc_id: str
    links: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PdfWorkResult:
    """一次工作项执行后的确定性三分类结果。"""

    succeeded: tuple[PdfWorkItem, ...]
    skipped: tuple[PdfWorkItem, ...]
    failed: tuple[PdfWorkItem, ...]


def execute_pdf_work_items(
    items: Sequence[PdfWorkItem],
    *,
    automatic_retry_rounds: int,
    local_fulltext_exists: Callable[[PdfWorkItem], bool],
    download: Callable[[PdfWorkItem], bool],
    progress: Callable[[Sequence[PdfWorkItem]], None] | None = None,
) -> PdfWorkResult:
    """并发执行工作项，隐藏跳过、有限重试和剩余项快照。

    ``download`` 抛出的异常立即向上传播；调用者应在进入 runner 前保存待处理项，
    使异常中断仍可从 checkpoint 恢复，避免把未知异常误判为普通下载失败。
    """
    indexed = list(enumerate(items))
    states: dict[int, str] = {}
    unfinished: set[int] = set()

    for index, current in indexed:
        if local_fulltext_exists(current):
            states[index] = "skipped"
        else:
            unfinished.add(index)

    items_by_index = dict(indexed)
    success_count = 0

    def run_stage(stage_items: list[tuple[int, PdfWorkItem]]) -> list[int]:
        """并发执行一个下载阶段并返回仍失败的原始索引。"""
        nonlocal success_count
        failed_indexes: list[int] = []
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {executor.submit(download, current): index for index, current in stage_items}
            for future in as_completed(futures):
                index = futures[future]
                if future.result():
                    states[index] = "succeeded"
                    unfinished.discard(index)
                    success_count += 1
                    if progress and success_count % _PROGRESS_INTERVAL == 0:
                        progress(tuple(items_by_index[i] for i in sorted(unfinished)))
                else:
                    states[index] = "failed"
                    failed_indexes.append(index)
        return sorted(failed_indexes)

    failed_indexes = run_stage(
        [(index, current) for index, current in indexed if index in unfinished]
    )

    for _ in range(automatic_retry_rounds):
        retryable = [
            (index, items_by_index[index])
            for index in failed_indexes
            if items_by_index[index].links
        ]
        if not retryable:
            break
        failed_indexes = run_stage(retryable)

    def select(state: str) -> tuple[PdfWorkItem, ...]:
        """按输入顺序选取进入指定终态的条目。"""
        return tuple(
            items_by_index[index] for index in sorted(items_by_index) if states.get(index) == state
        )

    return PdfWorkResult(
        succeeded=select("succeeded"),
        skipped=select("skipped"),
        failed=select("failed"),
    )


def _parse_pdf_work_item(raw: Any) -> PdfWorkItem | None:
    """把 checkpoint 中的单条 JSON 对象恢复为工作项；非法字段返回 None。"""
    if not isinstance(raw, dict):
        logger.warning("PDF 检查点单项不是对象，已跳过: %s", type(raw).__name__)
        return None
    pmid = raw.get("pmid")
    pmc_id = raw.get("pmc_id")
    links = raw.get("links")
    if (
        not isinstance(pmid, str)
        or not pmid
        or not isinstance(pmc_id, str)
        or not pmc_id
        or not isinstance(links, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in links.items()
        )
    ):
        logger.warning(
            "PDF 检查点单项字段无效，已跳过: pmid_type=%s, pmc_id_type=%s, links_type=%s",
            type(pmid).__name__,
            type(pmc_id).__name__,
            type(links).__name__,
        )
        return None
    return PdfWorkItem(pmid=pmid, pmc_id=pmc_id, links=dict(links))


def load_pdf_work_checkpoint(path: Path) -> tuple[PdfWorkItem, ...] | None:
    """读取并验证 checkpoint；缺失、不可解析或载体 shape 错误返回 None。"""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("PDF 检查点读取失败，将回退失败清单: %s", type(exc).__name__)
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("pending"), list):
        logger.warning("PDF 检查点结构无效，将回退失败清单")
        return None

    parsed = [_parse_pdf_work_item(raw) for raw in payload["pending"]]
    return tuple(current for current in parsed if current is not None)


def save_pdf_work_checkpoint(path: Path, items: Sequence[PdfWorkItem]) -> None:
    """原子保存工作项身份，不写执行期路径或 future。"""
    payload = {
        "pending": [
            {
                "pmid": current.pmid,
                "pmc_id": current.pmc_id,
                "links": dict(current.links),
            }
            for current in items
        ],
        "updated_at": datetime.now().isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    temporary.replace(path)
    logger.info("PDF 检查点已保存: 待重试 %d 篇", len(items))
