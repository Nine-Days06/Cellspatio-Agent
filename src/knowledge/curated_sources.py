"""非文献 curated 数据源：CellMarker / Cell Ontology 的下载缓存与文本化

- ensure_file：URL → 本地缓存文件（已存在跳过下载）
- _pack_entries：条目贪心装箱为定长文档
- cellmarker_texts / cell_ontology_texts：源文件 → 入库文档列表
"""
from pathlib import Path

import httpx

CELLMARKER_URL = (
    "http://117.50.127.228/CellMarker/CellMarker_download_files/file/"
    "Cell_marker_Human.xlsx"
)

# 本地缓存位置（data/raw 已 gitignore；源文件已预下载，ensure_file 命中即跳过）
ROOT = Path(__file__).resolve().parents[2]
CELLMARKER_PATH = ROOT / "data" / "raw" / "cellmarker" / "Cell_marker_Human.xlsx"

# 文档头：必须与 rebuild_kb.CURATED_HEADERS 一致，否则清库守卫会误判为文献
CELLMARKER_HEADER = "# CellMarker 细胞类型:"


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
        for _ in range(retries):
            try:
                resp = client.get(url)
                resp.raise_for_status()
                tmp = path.with_name(path.name + ".part")
                tmp.write_bytes(resp.content)
                tmp.replace(path)
                return path
            except (httpx.HTTPError, OSError) as exc:
                last = exc
        raise RuntimeError(f"下载失败（已重试 {retries} 次）: {url}: {last}") from last
    finally:
        if own_client:
            client.close()
