# downloader/pubmed_downloader.py
"""
NCBI E-utilities 批量下载器
流程：
  1. esearch  — 用查询词检索，获取 WebEnv + QueryKey（服务器端缓存）
  2. esearch  — 分页抓取全部 PMID 列表
  3. efetch   — 按批次下载 XML，保存到 raw_xml 目录
  4. 断点续传 — 已下载的批次自动跳过
"""

import re
import time
import json
import threading
import requests
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import (
    NCBI_API_KEY, NCBI_EMAIL, PROXY,
    REQUEST_INTERVAL, EFETCH_BATCH_SIZE,
    PUBMED_QUERY, RAW_XML_DIR,
    SEARCH_YEAR_MIN, SEARCH_YEAR_MAX, SEARCH_SLICE_YEARS,
)
from utils.logger import get_logger
from utils import db as dbutil

logger = get_logger("downloader")

# 全局速率限制器，保证并发请求不超出 NCBI API 频率限制
class _RateLimiter:
    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            now = time.time()
            elapsed = now - self._last
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self._last = time.time()

_rate_limiter = _RateLimiter(REQUEST_INTERVAL)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ── 内部工具函数 ──────────────────────────────────────────────

def _base_params() -> dict:
    p = {"email": NCBI_EMAIL, "tool": "cellspatio_lit_pipeline"}
    if NCBI_API_KEY:
        p["api_key"] = NCBI_API_KEY
    return p


def _get(url: str, params: dict, retries: int = 5) -> requests.Response:
    """带重试的 GET 请求（处理 429 / 5xx）"""
    kwargs = {"timeout": 60}
    if PROXY:
        kwargs["proxies"] = {"http": PROXY, "https": PROXY}
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, **kwargs)
            if r.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries:
                raise
            logger.warning(f"Request failed ({e}), retrying {attempt}/{retries}")
            time.sleep(2 ** attempt)


def _safe_json(r: requests.Response) -> dict:
    """处理 NCBI 可能返回的带非法控制字符的 JSON"""
    try:
        return r.json()
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError) as e:
        logger.warning(f"检测到非法 JSON 响应，尝试修复... ({e})")
        # 常见问题：JSON 字符串中包含未转义的换行符
        # 将原始控制字符替换为转义后的（特别是换行符）
        # 这里简单起见，先把 \n \r 替换掉，因为它们最常导致解析失败
        text = r.text.replace('\n', '\\n').replace('\r', '\\r')
        # 同时也移除其他不可见控制字符
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        try:
            return json.loads(text)
        except Exception as e2:
            logger.error(f"修复后仍无法解析 JSON。原始片段: {r.text[:500]}")
            raise e2


# ── Step 1 & 2：esearch 获取 PMID 列表 ───────────────────────

def _generate_year_slices(slice_size: int = 5) -> list[tuple[int, int]]:
    """将全局年份范围按 slice_size 切分为多个子区间"""
    slices = []
    start = SEARCH_YEAR_MIN
    while start <= SEARCH_YEAR_MAX:
        end = min(start + slice_size - 1, SEARCH_YEAR_MAX)
        slices.append((start, end))
        start = end + 1
    return slices


def fetch_pmid_list(query: str = PUBMED_QUERY,
                    mindate: int | None = None,
                    maxdate: int | None = None) -> list[str]:
    """
    通过 esearch + efetch 获取全部匹配的 PMID。
    注意：esearch 的 retstart 限制为 9999，
    超过 10000 条需利用 WebEnv + efetch(rettype='uilist') 获取。
    """
    logger.info("开始 esearch，获取 WebEnv ...")
    lo = mindate if mindate is not None else SEARCH_YEAR_MIN
    hi = maxdate if maxdate is not None else SEARCH_YEAR_MAX
    params = {
        **_base_params(),
        "db": "pubmed",
        "term": query,
        "datetype": "pdat",
        "mindate": str(lo),
        "maxdate": str(hi),
        "usehistory": "y",
        "retmax": 0,
        "rettype": "json",
        "retmode": "json",
    }
    r = _get(f"{EUTILS_BASE}/esearch.fcgi", params)
    result = _safe_json(r)["esearchresult"]
    
    if "ERROR" in result:
        logger.error(f"NCBI Search Error: {result['ERROR']}")
        raise RuntimeError(result["ERROR"])

    total    = int(result["count"])
    webenv   = result["webenv"]
    querykey = result["querykey"]
    logger.info(f"共找到 {total} 篇文献（WebEnv={webenv[:20]}...）")

    # 分页拉取 PMID
    pmids = []
    # 使用 efetch (uilist) 拉取，绕过 esearch 的 10k 限制
    # 注意：即便使用 efetch，PubMed 对于 retstart + retmax 也有 10,000 的硬限制
    page_size = 5000 
    for start in range(0, total, page_size):
        if start >= 10000:
            logger.warning(f"检测到结果数 ({total}) 超过 PubMed API 的 10,000 条分页限制。")
            logger.warning("仅能获取前 10,000 条记录。如需更多，请尝试分年份搜索。")
            break
            
        p = {
            **_base_params(),
            "db": "pubmed",
            "webenv": webenv,
            "query_key": querykey,
            "retstart": start,
            "retmax": page_size,
            "rettype": "uilist",
            "retmode": "text",
        }
        try:
            batch_r = _get(f"{EUTILS_BASE}/efetch.fcgi", p)
            # efetch (uilist) 返回纯文本，每行一个 PMID
            batch_ids = [line.strip() for line in batch_r.text.splitlines() if line.strip()]
            pmids.extend(batch_ids)
            logger.info(f"  PMID 列表进度: {len(pmids)}/{total}")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                logger.warning(f"分页请求失败 (retstart={start})，可能触发了 NCBI 的 10k 限制。停止获取 PMID。")
                break
            raise
        time.sleep(REQUEST_INTERVAL)

    logger.info(f"PMID 列表获取完毕，共 {len(pmids)} 条")
    return pmids


# ── Step 3：efetch 批量下载 XML ───────────────────────────────

def _download_single_batch(
    batch_idx: int,
    chunk: list[str],
    out_dir: Path,
    batch_size: int,
    total_batches: int,
) -> Path | None:
    """下载单个批次，由线程池调用，受全局速率限制器控制"""
    batch_file = out_dir / f"batch_{batch_idx:05d}.xml"

    if batch_file.exists() and batch_file.stat().st_size > 0:
        logger.info(f"  [批次 {batch_idx+1}/{total_batches}] 已存在，跳过")
        return batch_file

    params = {
        **_base_params(),
        "db": "pubmed",
        "id": ",".join(chunk),
        "rettype": "xml",
        "retmode": "xml",
    }

    _rate_limiter.wait()
    try:
        r = _get(f"{EUTILS_BASE}/efetch.fcgi", params)
        with open(batch_file, "wb") as f:
            f.write(r.content)
        logger.info(
            f"  [批次 {batch_idx+1}/{total_batches}] "
            f"下载 {len(chunk)} 篇 → {batch_file.name} "
            f"({batch_file.stat().st_size / 1024:.1f} KB)"
        )
        return batch_file
    except Exception as e:
        logger.error(f"  [批次 {batch_idx+1}] 下载失败: {e}")
        return None


def download_xml_batches(
    pmids: list[str],
    out_dir: Path = RAW_XML_DIR,
    batch_size: int = EFETCH_BATCH_SIZE,
) -> list[Path]:
    """
    将 PMID 列表分批，通过 efetch 并发下载 PubmedArticleSet XML。
    已存在的批次文件自动跳过（断点续传）。
    返回所有成功下载的批次文件路径。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 保存 PMID 列表备用（便于后续复查）
    pmid_list_path = out_dir / "pmid_list.json"
    with open(pmid_list_path, "w") as f:
        json.dump({"query": PUBMED_QUERY, "total": len(pmids), "pmids": pmids}, f)
    logger.info(f"PMID 列表已保存至 {pmid_list_path}")

    total_batches = (len(pmids) + batch_size - 1) // batch_size
    max_workers = 3 if not NCBI_API_KEY else 8

    chunks = [
        pmids[i * batch_size : (i + 1) * batch_size]
        for i in range(total_batches)
    ]

    xml_files = []
    futures = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for batch_idx, chunk in enumerate(chunks):
            future = executor.submit(
                _download_single_batch, batch_idx, chunk,
                out_dir, batch_size, total_batches
            )
            futures[future] = batch_idx

        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                xml_files.append(result)

    xml_files.sort()
    logger.info(f"下载完成，共 {len(xml_files)} 个批次文件")
    return xml_files


# ── 公开入口 ──────────────────────────────────────────────────

def run_download(query: str = PUBMED_QUERY) -> list[Path]:
    """完整下载流程入口，返回所有 XML 文件路径"""
    logger.info("=" * 60)
    logger.info("阶段一：批量下载 PubMed 文献")
    logger.info(f"搜索词: {query[:80]}...")
    logger.info("=" * 60)

    slices = _generate_year_slices(SEARCH_SLICE_YEARS)
    logger.info(f"年份切片: {SEARCH_SLICE_YEARS} 年/段，共 {len(slices)} 段")

    all_pmids = []
    for idx, (lo, hi) in enumerate(slices, 1):
        logger.info(f"--- 切片 {idx}/{len(slices)}: {lo}-{hi} ---")
        pmids = fetch_pmid_list(query, mindate=lo, maxdate=hi)
        all_pmids.extend(pmids)
        logger.info(f"  切片累计: {len(all_pmids)} 篇")

    all_pmids = list(dict.fromkeys(all_pmids))
    logger.info(f"所有切片处理完毕，去重后共 {len(all_pmids)} 篇")

    xml_files = download_xml_batches(all_pmids)
    return xml_files
