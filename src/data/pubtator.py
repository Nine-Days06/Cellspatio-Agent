"""PubTator3 取数：PMID → 规范基因 token（NCBIGene CURIE）+ 本地缓存

用 export API（GET、逗号批量、≤100/次、官方 ≤3 req/s、无 key）查文献基因。
与 FTP dump（gene2pubtator3.gz，月更）同源同批标注，但 API 更新更及时。

缓存：data/raw/pubtator/gene_ids.json（预计算一次，导入侧读取注入）。
"""
import json
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import httpx

from src.data.gene_map import gene_curie

logger = logging.getLogger(__name__)

EXPORT_URL = (
    "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/pubtator"
)
ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = ROOT / "data" / "raw" / "pubtator" / "gene_ids.json"
BATCH_SIZE = 100          # API 硬上限（实测 >100 返回 400）
MIN_INTERVAL = 1 / 3      # 官方 ≤3 req/s


def _parse_genes(text: str) -> dict[str, list[str]]:
    """解析 pubtator 导出：仅取 6 列标注行中 Type==Gene 的 GeneID（CURIE，去重保序）"""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) == 6 and cols[4] == "Gene" and cols[5].isdigit():
            curies = out.setdefault(cols[0], [])
            curie = gene_curie(cols[5])
            if curie not in curies:
                curies.append(curie)
    return out


def _request_batch(client: httpx.Client, batch: list[str]) -> dict[str, list[str]] | None:
    """取一批（≤100）；整批不存在时 API 返回 400 → 返回 None 由调用方拆半"""
    url = f"{EXPORT_URL}?pmids={','.join(batch)}"
    resp = client.get(url)
    if resp.status_code == 400:
        return None
    resp.raise_for_status()
    return _parse_genes(resp.text)


def _resolve_batch(
    get: Callable[[list[str]], dict[str, list[str]] | None], batch: list[str]
) -> dict[str, list[str]]:
    """单批：命中直接返回；400 且 >1 条则拆半递归；单条仍 400 则跳过"""
    parsed = get(batch)
    if parsed is not None:
        return parsed
    if len(batch) <= 1:
        logger.warning("PubTator3 无该 PMID，跳过: %s", batch[0] if batch else "")
        return {}
    mid = len(batch) // 2
    left = _resolve_batch(get, batch[:mid])
    left.update(_resolve_batch(get, batch[mid:]))
    return left


def fetch_pmid_genes(
    pmids: Iterable[str],
    *,
    client: httpx.Client | None = None,
    batch_size: int = BATCH_SIZE,
    min_interval: float = MIN_INTERVAL,
) -> dict[str, list[str]]:
    """批量查 PubTator3，返回 PMID → [NCBIGene CURIE]

    分批串行请求；所有请求（含 400 拆半递归的每一次）都经由节流闭包
    按 min_interval 限速；单条仍 400 则跳过。
    """
    pmids = [str(p) for p in pmids]
    own = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=True)
    out: dict[str, list[str]] = {}
    last = [0.0]

    def get(batch: list[str]) -> dict[str, list[str]] | None:
        if min_interval:
            wait = min_interval - (time.monotonic() - last[0])
            if wait > 0:
                time.sleep(wait)
        last[0] = time.monotonic()
        return _request_batch(client, batch)

    try:
        for start in range(0, len(pmids), batch_size):
            out.update(_resolve_batch(get, pmids[start:start + batch_size]))
    finally:
        if own:
            client.close()
    return out


def _resolve_path(path: Path | None) -> Path:
    """路径缺省时用默认缓存位置"""
    return Path(path) if path else CACHE_PATH


def load_pmid_genes(path: Path | None = None) -> dict[str, list[str]]:
    """读本地缓存；不存在或损坏返回 {}（导入侧据此不注入，不中断）"""
    path = _resolve_path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("PubTator 缓存损坏，按空处理: %s", path)
        return {}
    return {
        str(k): [x for x in v if isinstance(x, str)]
        for k, v in data.items()
        if isinstance(v, list)
    }


def save_pmid_genes(mapping: dict[str, list[str]], path: Path | None = None) -> None:
    """写缓存（JSON，覆盖）"""
    path = _resolve_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
