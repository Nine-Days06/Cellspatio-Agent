"""通用下载缓存：URL → 本地文件（重试 + 原子落盘）

数据层基础设施：fetcher 与知识层的 curated 源共用，避免 data 层反向依赖 knowledge 层。
"""
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


def ensure_file(
    url: str,
    path: Path,
    *,
    retries: int = 3,
    timeout: int = 300,
    client: httpx.Client | None = None,
) -> Path:
    """下载 url 到 path；已存在直接返回（跳过下载）

    先写 .part 再原子替换，避免半截文件被当成缓存命中；
    连续失败 retries 次后抛 RuntimeError。
    """
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        last: Exception | None = None
        for attempt, _ in enumerate(range(retries), start=1):
            try:
                resp = client.get(url)
                resp.raise_for_status()
                tmp = path.with_name(path.name + ".part")
                tmp.write_bytes(resp.content)
                tmp.replace(path)
                return path
            except (httpx.HTTPError, OSError) as exc:
                last = exc
                logger.warning("下载失败（第 %d/%d 次，将重试）: %s: %s", attempt, retries, url, last)
        raise RuntimeError(f"下载失败（已重试 {retries} 次）: {url}: {last}") from last
    finally:
        if own_client:
            client.close()
