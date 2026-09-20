"""PubMed 批量下载器 - 支持年份切片、并发下载、断点续传"""
import re
import time
import json
import threading
import requests
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import (
    NCBI_API_KEY, NCBI_EMAIL,
    REQUEST_INTERVAL, EFETCH_BATCH_SIZE,
    PUBMED_QUERY, RAW_XML_DIR,
    SEARCH_YEAR_MIN, SEARCH_YEAR_MAX, SEARCH_SLICE_YEARS,
)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class _RateLimiter:
    """全局速率限制器，保证并发请求不超出 NCBI API 频率限制"""
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


def _base_params() -> dict:
    """基础请求参数"""
    p = {"email": NCBI_EMAIL, "tool": "multiomics_lit_pipeline"}
    if NCBI_API_KEY:
        p["api_key"] = NCBI_API_KEY
    return p


def _get(url: str, params: dict, retries: int = 5) -> requests.Response:
    """带重试的 GET 请求（处理 429 / 5xx）"""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"  Rate limited, waiting {wait}s (attempt {attempt})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries:
                raise
            print(f"  Request failed ({e}), retrying {attempt}/{retries}")
            time.sleep(2 ** attempt)


def _safe_json(r: requests.Response) -> dict:
    """处理 NCBI 可能返回的带非法控制字符的 JSON"""
    try:
        return r.json()
    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
        text = r.text.replace('\n', '\\n').replace('\r', '\\r')
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        return json.loads(text)


# ── 年份切片 ──────────────────────────────────────────────────

def _generate_year_slices(slice_size: int = None) -> list[tuple[int, int]]:
    """将全局年份范围按 slice_size 切分为多个子区间"""
    slice_size = slice_size or SEARCH_SLICE_YEARS
    slices = []
    start = SEARCH_YEAR_MIN
    while start <= SEARCH_YEAR_MAX:
        end = min(start + slice_size - 1, SEARCH_YEAR_MAX)
        slices.append((start, end))
        start = end + 1
    return slices


# ── esearch 获取 PMID 列表 ────────────────────────────────────

def fetch_pmid_list(query: str = PUBMED_QUERY,
                    mindate: int | None = None,
                    maxdate: int | None = None) -> list[str]:
    """
    通过 esearch + efetch 获取全部匹配的 PMID。
    超过 10000 条需利用 WebEnv + efetch(rettype='uilist') 获取。
    """
    print("  开始 esearch，获取 WebEnv ...")
    lo = mindate if mindate is not None else SEARCH_YEAR_MIN
    hi = maxdate if maxdate is not None else SEARCH_YEAR_MAX

    # Step 1: esearch 获取 WebEnv
    p = {
        **_base_params(),
        "db": "pubmed",
        "term": query,
        "retmax": 0,
        "retmode": "json",
        "datetype": "pdat",
        "mindate": str(lo),
        "maxdate": str(hi),
    }
    r = _get(f"{EUTILS_BASE}/esearch.fcgi", p)
    result = _safe_json(r).get("esearchresult", {})

    total    = int(result["count"])
    webenv   = result["webenv"]
    querykey = result["querykey"]
    print(f"  共找到 {total} 篇文献（{lo}-{hi}）")

    if total == 0:
        return []

    # Step 2: 分页拉取 PMID
    pmids = []
    page_size = 5000
    for start in range(0, total, page_size):
        if start >= 10000:
            print(f"  警告: 结果数 ({total}) 超过 10,000 条限制，仅获取前 10,000 条")
            break

        _rate_limiter.wait()
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
            batch_ids = [line.strip() for line in batch_r.text.splitlines() if line.strip()]
            pmids.extend(batch_ids)
            print(f"    PMID 列表进度: {len(pmids)}/{total}")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                print(f"  警告: 分页请求失败，可能触发了 NCBI 的 10k 限制")
                break
            raise

    print(f"  PMID 列表获取完毕，共 {len(pmids)} 条")
    return pmids


# ── efetch 批量下载 XML ───────────────────────────────────────

def _download_single_batch(
    batch_idx: int,
    chunk: list[str],
    out_dir: Path,
    batch_size: int,
    total_batches: int,
) -> Path | None:
    """下载单个批次"""
    batch_file = out_dir / f"batch_{batch_idx:05d}.xml"

    # 断点续传：已存在则跳过
    if batch_file.exists() and batch_file.stat().st_size > 0:
        print(f"    [批次 {batch_idx+1}/{total_batches}] 已存在，跳过")
        return batch_file

    _rate_limiter.wait()
    params = {
        **_base_params(),
        "db": "pubmed",
        "id": ",".join(chunk),
        "retmode": "xml",
    }
    try:
        r = _get(f"{EUTILS_BASE}/efetch.fcgi", params)
        batch_file.write_bytes(r.content)
        print(f"    [批次 {batch_idx+1}/{total_batches}] 下载完成 ({len(chunk)} 篇)")
        return batch_file
    except Exception as e:
        print(f"    [批次 {batch_idx+1}/{total_batches}] 下载失败: {e}")
        return None


def download_xml_batches(pmids: list[str], out_dir: Path = None) -> list[Path]:
    """并发下载 XML 批次文件"""
    out_dir = out_dir or RAW_XML_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if not pmids:
        print("  无 PMID 需要下载")
        return []

    # 分批
    chunks = [pmids[i:i + EFETCH_BATCH_SIZE] for i in range(0, len(pmids), EFETCH_BATCH_SIZE)]
    total_batches = len(chunks)
    print(f"  开始下载 {len(pmids)} 篇文献，共 {total_batches} 个批次")

    xml_files = []
    # 限制并发数为 4，避免触发 NCBI 限流
    max_workers = min(4, total_batches)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_download_single_batch, i, chunk, out_dir, EFETCH_BATCH_SIZE, total_batches): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                xml_files.append(result)

    xml_files.sort()
    print(f"  下载完成，共 {len(xml_files)} 个批次文件")
    return xml_files


# ── 公开入口 ──────────────────────────────────────────────────

def run_download(query: str = PUBMED_QUERY) -> list[Path]:
    """完整下载流程入口，支持年份切片，返回所有 XML 文件路径"""
    print("=" * 60)
    print("阶段一：批量下载 PubMed 文献")
    print(f"搜索词: {query[:80]}...")
    print("=" * 60)

    # 年份切片
    slices = _generate_year_slices()
    print(f"年份切片: {SEARCH_SLICE_YEARS} 年/段，共 {len(slices)} 段")

    all_pmids = []
    for idx, (lo, hi) in enumerate(slices, 1):
        print(f"--- 切片 {idx}/{len(slices)}: {lo}-{hi} ---")
        pmids = fetch_pmid_list(query, mindate=lo, maxdate=hi)
        all_pmids.extend(pmids)
        print(f"  切片累计: {len(all_pmids)} 篇")

    # 去重
    all_pmids = list(dict.fromkeys(all_pmids))
    print(f"所有切片处理完毕，去重后共 {len(all_pmids)} 篇")

    # 下载 XML
    xml_files = download_xml_batches(all_pmids)
    return xml_files
