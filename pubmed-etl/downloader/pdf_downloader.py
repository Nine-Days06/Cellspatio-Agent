# downloader/pdf_downloader.py
"""
PMC Open Access PDF 下载器
流程：
  1. 从数据库读取 LLM 判定相关的带有 PMC ID 的文献。
  2. 调用 PMC OA API 获取这些 PMC ID 对应的 PDF 下载链接。
  3. 转换为 HTTPS 链接并下载到指定目录。
  4. 支持断点续传（跳过已存在文件）。
"""

import time
import re
import tarfile
import io
import csv
import json
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from lxml import etree
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.settings import (
    DB_PATH, PMC_OA_API, PDF_DIR,
    REQUEST_INTERVAL, OUTPUT_DIR, PROXY
)
from utils.db import get_conn
from utils.logger import get_logger

logger = get_logger("pdf_downloader")

MIN_VALID_FILE_BYTES = 1024

OA_FETCH_MAX_WORKERS = 8
DOWNLOAD_MAX_WORKERS = 8
OA_LINKS_CACHE_GLOB = "oa_download_links_*.csv"
ARIA2C_CONNECT_PER_SERVER = "8"
ARIA2C_SPLIT = "8"
ARIA2C_MIN_SPLIT_SIZE = "1M"
PDF_CHECKPOINT_FILENAME = "pdf_download_progress.json"
RETRY_CHECKPOINT_INTERVAL = 10

# ── API 调用与解析 ───────────────────────────────────────────

def normalize_pmc_asset_url(url: str) -> str:
    """
    将 OA API 返回的资源链接标准化为当前可访问的 HTTPS 路径。

    背景：PMC 在 2026-04 调整了 FTP/Cloud 目录结构，旧路径
    /pub/pmc/... 需迁移到 /pub/pmc/deprecated/...。
    """
    if not url:
        return ""

    normalized = url.strip()
    if normalized.startswith("ftp://"):
        normalized = "https://" + normalized[len("ftp://"):]

    old_prefix = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/"
    new_prefix = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/"
    if normalized.startswith(old_prefix) and not normalized.startswith(new_prefix):
        normalized = normalized.replace(old_prefix, new_prefix, 1)

    return normalized


def normalize_pmc_id(value: str) -> str | None:
    """
    规范化并校验 PMC ID。
    - 纯数字：补齐为 `PMC{digits}`
    - `PMC` 前缀：统一转大写
    - 仅接受 `PMC` + 数字格式
    """
    if not value:
        return None

    normalized = value.strip().upper()
    if not normalized:
        return None

    if normalized.isdigit():
        normalized = f"PMC{normalized}"

    if not re.fullmatch(r"PMC\d+", normalized):
        return None

    return normalized

def _request_oa_with_retry(
    url: str, params: dict | list, max_retries: int = 3, timeout: int = 45
) -> requests.Response | None:
    """带指数退避重试的 OA API GET 请求。429/5xx 可重试，其他 4xx 不重试。"""
    kwargs: dict = {"params": params, "timeout": timeout}
    if PROXY:
        kwargs["proxies"] = {"http": PROXY, "https": PROXY}
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"OA API rate limited (429), waiting {wait}s (attempt {attempt}/{max_retries})")
                time.sleep(wait)
                continue
            if 500 <= r.status_code < 600:
                wait = 2 ** attempt
                logger.warning(f"OA API server error ({r.status_code}), waiting {wait}s (attempt {attempt}/{max_retries})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.Timeout:
            if attempt == max_retries:
                logger.warning(f"OA API timeout after {max_retries} retries: {url[:60]}...")
                return None
            wait = 2 ** attempt
            logger.warning(f"OA API timeout, waiting {wait}s (attempt {attempt}/{max_retries})")
            time.sleep(wait)
        except requests.ConnectionError as e:
            if attempt == max_retries:
                logger.warning(f"OA API connection error after {max_retries} retries: {e}")
                return None
            wait = 2 ** attempt
            logger.warning(f"OA API connection error, waiting {wait}s (attempt {attempt}/{max_retries}): {e}")
            time.sleep(wait)
        except requests.RequestException as e:
            logger.warning(f"OA API request failed: {e}")
            return None
    return None


def _fetch_single_oa_link(pmc_id: str) -> tuple[str, dict[str, str] | None, str]:
    """
    查询单个 PMCID 的 OA 资源链接。
    返回 (pmc_id, links | None, status)，status ∈ {"ok", "not_oa", "network_fail"}。
    """
    parser = etree.XMLParser(recover=True)

    r = _request_oa_with_retry(PMC_OA_API, params={"id": pmc_id}, timeout=30)
    if r is None:
        logger.warning(f"  单条查询失败（网络）: {pmc_id}（API 请求失败）")
        return pmc_id, None, "network_fail"

    try:
        root = etree.fromstring(r.content, parser=parser)
    except Exception as e:
        logger.warning(f"  单条查询 XML 解析失败: {pmc_id} -> {e}")
        return pmc_id, None, "network_fail"

    error = root.find(".//error")
    if error is not None and error.get("code") in ("idIsNotOpenAccess", "idDoesNotExist"):
        return pmc_id, None, "not_oa"

    record = root.find(".//record")
    if record is None and root.tag == "record":
        record = root
    if record is None:
        return pmc_id, None, "network_fail"

    links: dict[str, str] = {}
    pdf_link_node = record.find(".//link[@format='pdf']")
    if pdf_link_node is not None and pdf_link_node.get("href"):
        links["pdf"] = normalize_pmc_asset_url(pdf_link_node.get("href"))

    tgz_link_node = record.find(".//link[@format='tgz']")
    if tgz_link_node is not None and tgz_link_node.get("href"):
        links["tgz"] = normalize_pmc_asset_url(tgz_link_node.get("href"))

    if not links:
        return pmc_id, None, "network_fail"

    return pmc_id, links, "ok"


def _extract_links_from_record(record) -> tuple[str | None, dict[str, str] | None]:
    record_id = record.get("id") or record.get("pmcid") or record.get("pmc_id")
    normalized_id = normalize_pmc_id(record_id) if record_id else None
    if not normalized_id:
        return None, None

    links: dict[str, str] = {}
    pdf_link_node = record.find(".//link[@format='pdf']")
    if pdf_link_node is not None and pdf_link_node.get("href"):
        links["pdf"] = normalize_pmc_asset_url(pdf_link_node.get("href"))

    tgz_link_node = record.find(".//link[@format='tgz']")
    if tgz_link_node is not None and tgz_link_node.get("href"):
        links["tgz"] = normalize_pmc_asset_url(tgz_link_node.get("href"))

    if not links:
        return normalized_id, None

    return normalized_id, links


def _extract_error_pmc_ids(root) -> set[str]:
    error_ids: set[str] = set()
    for error in root.findall(".//error"):
        raw_candidates = []
        if error.get("id"):
            raw_candidates.append(error.get("id"))
        if error.text:
            raw_candidates.extend(re.findall(r"PMC\d+", error.text.upper()))

        for candidate in raw_candidates:
            normalized = normalize_pmc_id(candidate)
            if normalized:
                error_ids.add(normalized)

    return error_ids


def load_cached_oa_links(
    pmc_ids: list[str],
    out_dir: Path = OUTPUT_DIR,
) -> dict[str, dict[str, str]]:
    """
    从历史导出的 OA 链接清单中加载可复用链接。
    只返回当前 `pmc_ids` 范围内的记录。
    """
    if not pmc_ids:
        return {}

    target_ids = set(pmc_ids)
    cached_links: dict[str, dict[str, str]] = {}
    csv_files = sorted(out_dir.glob(OA_LINKS_CACHE_GLOB), reverse=True)

    for csv_file in csv_files:
        if len(cached_links) >= len(target_ids):
            break

        try:
            with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pmc_id = normalize_pmc_id(row.get("pmc_id", ""))
                    if not pmc_id or pmc_id not in target_ids or pmc_id in cached_links:
                        continue

                    links: dict[str, str] = {}
                    pdf_url = normalize_pmc_asset_url(row.get("pdf_url", "")) if row.get("pdf_url") else ""
                    tgz_url = normalize_pmc_asset_url(row.get("tgz_url", "")) if row.get("tgz_url") else ""
                    if pdf_url:
                        links["pdf"] = pdf_url
                    if tgz_url:
                        links["tgz"] = tgz_url

                    if links:
                        cached_links[pmc_id] = links
        except Exception as e:
            logger.warning(f"读取历史链接清单失败，已跳过: {csv_file} -> {e}")

    return cached_links


def fetch_oa_links(
    pmc_ids: list[str],
    cached_links: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """
    获取 PMCID 对应的 OA 资源链接（pdf/tgz）。
    `oa.fcgi` 仅支持单 ID 查询，这里用并发 + 速率控制逐条查询。
    返回 (链接字典, 网络失败 PMCID 列表)；非 OA 的 PMCID 不进任何结果。
    """
    if not pmc_ids:
        return {}, []

    uniq_pmc_ids = list(dict.fromkeys(pmc_ids))
    cached_links = cached_links or {}
    oa_map: dict[str, dict[str, str]] = {
        pmc_id: cached_links[pmc_id]
        for pmc_id in uniq_pmc_ids
        if pmc_id in cached_links
    }

    missing = [pmc_id for pmc_id in uniq_pmc_ids if pmc_id not in oa_map]
    if not missing:
        return oa_map, []

    logger.info(f"需要远程查询 {len(missing)} 个 PMCID（并发 {OA_FETCH_MAX_WORKERS} 线程，请求间隔 {REQUEST_INTERVAL}s）...")

    rate_lock = threading.Lock()
    last_ts = [0.0]

    def _rate_limited_fetch(pmc_id: str):
        with rate_lock:
            now = time.time()
            gap = REQUEST_INTERVAL - (now - last_ts[0])
            if gap > 0:
                time.sleep(gap)
            last_ts[0] = time.time()
        return _fetch_single_oa_link(pmc_id)

    network_failed: list[str] = []
    not_oa_count = 0

    with ThreadPoolExecutor(max_workers=OA_FETCH_MAX_WORKERS) as executor:
        future_to_pmc = {
            executor.submit(_rate_limited_fetch, pid): pid
            for pid in missing
        }

        for i, future in enumerate(as_completed(future_to_pmc), 1):
            pid, links, status = future.result()
            if status == "ok" and links:
                oa_map[pid] = links
            elif status == "network_fail":
                network_failed.append(pid)
            else:
                not_oa_count += 1
            if i % 20 == 0 or i == len(missing):
                logger.info(f"  获取 OA 链接进度: {i}/{len(missing)}")

    if not_oa_count:
        logger.info(f"  其中 {not_oa_count} 篇为非 OA 文献（无 OA 全文，正常跳过）。")
    if network_failed:
        logger.warning(f"  网络失败 {len(network_failed)} 篇，将进入待重试清单（可 --step pdf-retry 续跑）。")

    return oa_map, network_failed


def fetch_pdf_urls(pmc_ids: list[str]) -> dict[str, str]:
    """
    分批调用 PMC OA API 获取 PDF 下载链接。
    返回 {pmc_id: pdf_url} 字典。
    """
    cached_links = load_cached_oa_links(pmc_ids)
    oa_links, _ = fetch_oa_links(pmc_ids, cached_links=cached_links)
    return {pmc_id: links["pdf"] for pmc_id, links in oa_links.items() if "pdf" in links}


def export_oa_links_csv(
    oa_links: dict[str, dict[str, str]],
    pmc_to_info: dict[str, dict[str, str]],
    out_dir: Path = OUTPUT_DIR,
) -> Path:
    """
    导出本次获取到的 OA 下载链接清单（CSV）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"oa_download_links_{timestamp}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["pmid", "pmc_id", "pdf_url", "tgz_url"])

        for pmc_id in sorted(oa_links.keys()):
            info = pmc_to_info.get(pmc_id, {})
            links = oa_links[pmc_id]
            writer.writerow([
                info.get("pmid", ""),
                pmc_id,
                links.get("pdf", ""),
                links.get("tgz", ""),
            ])

    return csv_path

def export_failed_links_csv(
    failed_items: list[dict],
    out_dir: Path = OUTPUT_DIR,
) -> Path:
    """
    导出下载失败的链接清单（CSV），方便人工核查或补下载。
    每行包含 pmid、pmc_id、pdf_url、tgz_url、error 类型。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"failed_downloads_{timestamp}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["pmid", "pmc_id", "pdf_url", "tgz_url"])
        for item in failed_items:
            links = item["links"]
            writer.writerow([
                item.get("pmid", ""),
                item.get("pmc_id", ""),
                links.get("pdf", "") if links else "",
                links.get("tgz", "") if links else "",
            ])

    return csv_path


# ── PDF 下载 checkpoint ──────────────────────────────────────

def _pdf_checkpoint_path() -> Path:
    return Path(OUTPUT_DIR) / PDF_CHECKPOINT_FILENAME


def _save_pdf_checkpoint(pending: list[dict]) -> None:
    """保存待重试清单快照，崩溃后可恢复。"""
    data = {"pending": pending, "updated_at": datetime.now().isoformat()}
    path = _pdf_checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    logger.info(f"检查点已保存: 待重试 {len(pending)} 篇")


def _load_pdf_checkpoint() -> list[dict]:
    """加载待重试清单；文件缺失或损坏返回空列表。"""
    path = _pdf_checkpoint_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("pending", [])
    except Exception as e:
        logger.warning(f"PDF 检查点读取失败，将从头开始: {e}")
        return []


def _clear_pdf_checkpoint():
    """全部完成后删除检查点。"""
    path = _pdf_checkpoint_path()
    if path.exists():
        path.unlink()
        logger.info("PDF 检查点已清除（全部重试完成）")


def load_failed_items_from_csv(out_dir: Path = OUTPUT_DIR) -> list[dict]:
    """从最新 failed_downloads_*.csv 加载待重试清单。"""
    csv_files = sorted(Path(out_dir).glob("failed_downloads_*.csv"), reverse=True)
    if not csv_files:
        logger.info("未找到失败下载清单 failed_downloads_*.csv")
        return []

    csv_file = csv_files[0]
    logger.info(f"从失败清单加载重试项: {csv_file}")
    items: list[dict] = []
    try:
        with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                links: dict[str, str] = {}
                pdf_url = normalize_pmc_asset_url(row.get("pdf_url", ""))
                tgz_url = normalize_pmc_asset_url(row.get("tgz_url", ""))
                if pdf_url:
                    links["pdf"] = pdf_url
                if tgz_url:
                    links["tgz"] = tgz_url
                items.append({
                    "pmid": row.get("pmid", ""),
                    "pmc_id": normalize_pmc_id(row.get("pmc_id", "")) or "",
                    "links": links,
                })
    except Exception as e:
        logger.warning(f"读取失败清单 CSV 失败: {csv_file} -> {e}")
        return []
    return items


# ── 下载核心 ──────────────────────────────────────────────────

def _run_aria2c_download(url: str, output_path: Path, timeout_sec: int = 300) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "aria2c",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--file-allocation=none",
        "--summary-interval=0",
        "--console-log-level=warn",
        "--max-tries=3",
        "--retry-wait=2",
        "--connect-timeout=30",
        "--timeout=60",
        "-x", ARIA2C_CONNECT_PER_SERVER,
        "-s", ARIA2C_SPLIT,
        "-k", ARIA2C_MIN_SPLIT_SIZE,
        "-d", str(output_path.parent),
        "-o", output_path.name,
    ]
    if PROXY:
        cmd.append(f"--all-proxy={PROXY}")
    cmd.append(url)

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except FileNotFoundError:
        logger.error("未检测到 aria2c，请先安装并确保其在系统 PATH 中。")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"aria2c 下载超时: {url}")
        return False
    except Exception as e:
        logger.error(f"aria2c 执行异常: {url} -> {e}")
        return False

    if completed.returncode != 0:
        err_msg = (completed.stderr or completed.stdout or "").strip()
        if len(err_msg) > 300:
            err_msg = err_msg[-300:]
        logger.error(f"aria2c 下载失败: {url} -> {err_msg}")
        return False

    return output_path.exists()

def _cleanup_aria2_temp_files(temp_path: Path) -> None:
    """删除 aria2 下载中断残留的临时文件（含 .part.aria2 控制文件）。"""
    if temp_path.exists():
        temp_path.unlink()
    aria2_control = temp_path.with_name(temp_path.name + ".aria2")
    if aria2_control.exists():
        aria2_control.unlink()


def download_pdf_file(url: str, dest_path: Path) -> bool:
    """
    下载单个 PDF 文件，支持断点续传（检查是否存在）。
    成功返回 True，失败返回 False。
    """
    if dest_path.exists() and dest_path.stat().st_size > MIN_VALID_FILE_BYTES: 
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".pdf.part")

    try:
        if not _run_aria2c_download(url, temp_path):
            raise RuntimeError("aria2c 执行失败")

        if temp_path.stat().st_size <= MIN_VALID_FILE_BYTES:
            raise RuntimeError("下载文件过小，可能不是有效 PDF")

        temp_path.replace(dest_path)
        return True

    except Exception as e:
        logger.error(f"  下载失败: {url} -> {e}")

        _cleanup_aria2_temp_files(temp_path)
        if dest_path.exists() and dest_path.stat().st_size <= MIN_VALID_FILE_BYTES:
            dest_path.unlink()

        return False


def _select_article_pdf_member(members: list) -> tarfile.TarInfo | None:
    """
    从 OA tgz 包成员中选择正文 PDF。

    PMC OA 包中正文 PDF 通常与 .nxml/.xml 文件同名（如 BCR-**.pdf 对应 BCR-**.nxml），
    而配图/表格等补充材料 PDF 命名不同（如 *-s001.pdf）。按 stem 优先匹配可避免取错。
    """
    pdf_members = [
        m for m in members if m.isfile() and m.name.lower().endswith(".pdf")
    ]
    if not pdf_members:
        return None
    if len(pdf_members) == 1:
        return pdf_members[0]

    xml_stems = {
        Path(m.name).stem
        for m in members
        if m.isfile() and m.name.lower().endswith((".nxml", ".xml"))
    }
    if xml_stems:
        for pdf in pdf_members:
            if Path(pdf.name).stem in xml_stems:
                return pdf

    return pdf_members[0]


def extract_nxml_to_txt(tgz_path: Path, txt_path: Path) -> bool:
    """
    从 OA tgz 包中提取 nxml 正文全文并转存为纯文本 txt。
    包内无 nxml 时返回 False（极端情况，视为失败）。
    """
    try:
        nxml_name = None
        with tarfile.open(tgz_path, mode="r:gz") as tar:
            for m in tar.getmembers():
                if m.isfile() and m.name.lower().endswith(".nxml"):
                    nxml_name = m.name
                    break
            if nxml_name is None:
                logger.warning(f"  tgz 包内未找到 nxml: {tgz_path}")
                return False
            nxml_bytes = tar.extractfile(nxml_name).read()

        root = etree.fromstring(nxml_bytes, parser=etree.XMLParser(recover=True))
        lines: list[str] = []

        title_el = root.find(".//article-title")
        if title_el is not None:
            lines.append("TITLE: " + "".join(title_el.itertext()).strip())
            lines.append("")

        body = root.find(".//body")
        if body is not None:
            for sec in body.iter():
                tag = etree.QName(sec).localname if isinstance(sec.tag, str) else ""
                if tag == "title":
                    lines.append("")
                    lines.append("### " + "".join(sec.itertext()).strip())
                elif tag == "p":
                    text = "".join(sec.itertext()).strip()
                    if text:
                        lines.append(text)
                elif tag == "table-wrap":
                    for tr in sec.findall(".//tr"):
                        cells = [
                            "".join(td.itertext()).strip()
                            for td in tr.findall("td")
                        ]
                        if cells:
                            lines.append(" | ".join(cells))

        if not lines:
            return False

        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"  NXML->TXT 提取失败: {tgz_path} -> {e}")
        return False


def download_pdf_from_tgz(url: str, dest_path: Path) -> bool:
    """
    从 OA tgz 包中提取 PDF，并保存为 .pdf。
    当 `oa.fcgi` 未返回 pdf 直链但包内已有 PDF 时可作为回退策略。
    包内有多个 PDF 时优先选择与 .nxml/.xml 同名的正文 PDF。
    """
    if dest_path.exists() and dest_path.stat().st_size > MIN_VALID_FILE_BYTES:
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".pdf.part")
    tgz_temp_path = dest_path.with_suffix(".tgz.part")

    try:
        pdf_bytes = None
        if not _run_aria2c_download(url, tgz_temp_path):
            raise RuntimeError("aria2c 下载 tgz 失败")

        with tarfile.open(tgz_temp_path, mode="r:gz") as tar:
            pdf_member = _select_article_pdf_member(tar.getmembers())

            if pdf_member is None:
                # 包内无 PDF（仅 nxml）时回退：提取 XML 全文转 txt
                txt_path = dest_path.with_suffix(".txt")
                logger.info(f"  tgz 包内无 PDF，尝试提取 XML 全文: {txt_path.name}")
                tar.close()
                if extract_nxml_to_txt(tgz_temp_path, txt_path):
                    logger.info(f"  该文献无 PDF 全文，已提取 XML 全文: {txt_path}")
                    return True
                raise RuntimeError("tgz 包内未找到 PDF 且 XML 全文提取失败")

            extracted = tar.extractfile(pdf_member)
            if extracted is None:
                raise RuntimeError("无法读取 tgz 包内 PDF 文件")
            pdf_bytes = extracted.read()

        if not pdf_bytes or len(pdf_bytes) <= MIN_VALID_FILE_BYTES:
            raise RuntimeError("提取到的 PDF 过小，可能无效")

        with open(temp_path, "wb") as f:
            f.write(pdf_bytes)

        if temp_path.stat().st_size <= MIN_VALID_FILE_BYTES:
            raise RuntimeError("PDF 文件过小，可能提取失败")

        temp_path.replace(dest_path)
        return True
    except Exception as e:
        logger.error(f"  TGZ->PDF 提取失败: {url} -> {e}")
        _cleanup_aria2_temp_files(temp_path)
        if dest_path.exists() and dest_path.stat().st_size <= MIN_VALID_FILE_BYTES:
            dest_path.unlink()
        return False
    finally:
        _cleanup_aria2_temp_files(tgz_temp_path)


def download_oa_pdf(links: dict[str, str], pdf_path: Path) -> bool:
    """
    下载整篇正文 PDF，不做 txt 回退。
    优先 pdf 直链；无直链或直链失败时从 tgz 包内提取正文 PDF。
    返回是否成功。
    """
    pdf_url = links.get("pdf")
    tgz_url = links.get("tgz")

    if pdf_url and download_pdf_file(pdf_url, pdf_path):
        return True

    if tgz_url and download_pdf_from_tgz(tgz_url, pdf_path):
        return True

    return False

# ── 业务流程 ──────────────────────────────────────────────────

def run_pdf_download(db_path: Path = DB_PATH):
    """
    执行 PDF 下载主流程。
    """
    logger.info("=" * 60)
    logger.info("阶段四：下载 OA 全文（仅 PDF）")
    logger.info("=" * 60)

    # 1. 查找高/中相关且有 PMC ID 的文献
    query = """
    SELECT a.pmid, a.pmc_id
    FROM articles a
    JOIN llm_validation v ON a.pmid = v.pmid
    WHERE (v.human_review = 'Y' OR (v.human_review IS NULL AND v.llm_verdict = 'RELEVANT'))
      AND a.pmc_id IS NOT NULL AND a.pmc_id != ''
    """
    
    with get_conn(db_path) as conn:
        records = conn.execute(query).fetchall()
        
    if not records:
        logger.info("未发现符合下载条件（有 PMC ID 且 LLM 判定相关）的文献。")
        return

    logger.info(f"符合条件的文献共 {len(records)} 篇，开始获取下载链接...")

    # 2. 批量获取 PDF 链接
    pmc_to_info = {}
    invalid_pmc_count = 0
    for r in records:
        pid = normalize_pmc_id(r["pmc_id"])
        if not pid:
            invalid_pmc_count += 1
            continue
        pmc_to_info[pid] = {"pmid": r["pmid"]}

    if invalid_pmc_count:
        logger.warning(f"已跳过 {invalid_pmc_count} 条格式无效的 PMC ID。")
    
    pmc_ids = list(pmc_to_info.keys())
    cached_oa_links = load_cached_oa_links(pmc_ids)
    if cached_oa_links:
        logger.info(f"复用历史已获取链接 {len(cached_oa_links)} 条。")

    oa_links, network_failed = fetch_oa_links(pmc_ids, cached_links=cached_oa_links)
    pdf_link_count = sum(1 for links in oa_links.values() if "pdf" in links)
    tgz_link_count = sum(1 for links in oa_links.values() if "tgz" in links)

    logger.info(
        f"成功获取 {len(oa_links)} 条 OA 资源（PDF: {pdf_link_count}, TGZ: {tgz_link_count}）。"
    )

    links_csv = export_oa_links_csv(oa_links=oa_links, pmc_to_info=pmc_to_info)
    logger.info(f"已导出下载链接清单: {links_csv}")

    # 网络失败项并入 failed_items，后续 --step pdf-retry 重新查链并下载
    network_failed_items: list[dict] = []
    for pid in network_failed:
        info = pmc_to_info.get(pid, {})
        network_failed_items.append({
            "pmc_id": pid,
            "links": {},
            "pdf_path": PDF_DIR / f"{info.get('pmid', '')}.pdf",
            "pmid": info.get("pmid", ""),
        })

    # 3. 执行下载
    pdf_success_count = 0
    failed_count = 0
    skip_count = 0
    failed_items: list[dict] = []

    # 使用线程池并发下载，提高效率
    with ThreadPoolExecutor(max_workers=DOWNLOAD_MAX_WORKERS) as executor:
        future_to_meta: dict = {}
        for pmc_id, links in oa_links.items():
            info = pmc_to_info[pmc_id]
            dest_dir = PDF_DIR
            pdf_path = dest_dir / f"{info['pmid']}.pdf"

            if pdf_path.exists():
                skip_count += 1
                continue

            meta = {
                "pmc_id": pmc_id,
                "links": links,
                "pdf_path": pdf_path,
                "pmid": info["pmid"],
                }
            future = executor.submit(
                download_oa_pdf,
                links,
                pdf_path,
            )
            future_to_meta[future] = meta

        for future in as_completed(future_to_meta):
            ok = future.result()
            meta = future_to_meta[future]
            if ok:
                pdf_success_count += 1
            else:
                failed_count += 1
                failed_items.append(meta)

            if pdf_success_count > 0 and pdf_success_count % 10 == 0:
                logger.info(f"  已下载 {pdf_success_count} 篇 PDF...")

    # 网络失败项（无链接）计入失败清单，待 --step pdf-retry 重查链接
    failed_items = network_failed_items + failed_items
    failed_count += len(network_failed_items)

    logger.info(
        f"首次下载完成：PDF {pdf_success_count} 篇，"
        f"失败 {failed_count} 篇，跳过 {skip_count} 篇。"
    )

    # 4. 重试下载失败的链接（最多 2 次）
    # 网络失败项无链接，跳过自动重试，交由 --step pdf-retry 重查链接
    retryable = [item for item in failed_items if item["links"]]
    non_retryable = [item for item in failed_items if not item["links"]]
    MAX_RETRIES = 2
    retry_round = 0
    while retryable and retry_round < MAX_RETRIES:
        retry_round += 1
        retry_success_pdf = 0
        retry_fail: list[dict] = []

        logger.info(
            f"重试第 {retry_round}/{MAX_RETRIES} 轮，剩余 {len(retryable)} 篇（有链接）待重试..."
        )

        with ThreadPoolExecutor(max_workers=DOWNLOAD_MAX_WORKERS) as executor:
            retry_future_to_meta = {
                executor.submit(
                    download_oa_pdf,
                    item["links"],
                    item["pdf_path"],
                ): item
                for item in retryable
            }

            for future in as_completed(retry_future_to_meta):
                ok = future.result()
                if ok:
                    retry_success_pdf += 1
                else:
                    retry_fail.append(retry_future_to_meta[future])

        failed_items = non_retryable + retry_fail
        retryable = retry_fail
        logger.info(
            f"重试第 {retry_round} 轮完成：PDF {retry_success_pdf} 篇，"
            f"仍失败 {len(failed_items)} 篇。"
        )

    # 5. 导出最终失败链接列表
    if failed_items:
        failed_csv = export_failed_links_csv(failed_items, out_dir=OUTPUT_DIR)
        checkpoint_items = [
            {"pmid": item["pmid"], "pmc_id": item["pmc_id"], "links": item["links"]}
            for item in failed_items
        ]
        _save_pdf_checkpoint(checkpoint_items)
        logger.info(f"仍有 {len(failed_items)} 篇下载失败，失败链接清单: {failed_csv}，运行 --step pdf-retry 可续跑。")
    elif pdf_success_count + failed_count + skip_count > 0:
        _clear_pdf_checkpoint()
        logger.info("所有下载任务均已成功完成。")
    else:
        logger.info("本次无下载任务执行，保留既有检查点。")

    logger.info(f"存储位置: {PDF_DIR}")


# ── 失败重试（断点续传） ─────────────────────────────────────

def run_pdf_retry(db_path: Path = DB_PATH):
    """
    仅重试之前失败的 PDF 下载，支持断点续传。
    优先恢复 checkpoint；无 checkpoint 时读取最新失败清单 CSV。
    """
    logger.info("=" * 60)
    logger.info("阶段四补：重试失败的 OA 全文下载")
    logger.info("=" * 60)

    pending = _load_pdf_checkpoint()
    if pending:
        logger.info(f"从检查点恢复 {len(pending)} 篇待重试项。")
    else:
        pending = load_failed_items_from_csv()
        if not pending:
            logger.info("无检查点且无失败清单，没有可重试项。")
            return

    pending = [
        item for item in pending
        if not (item.get("pmid") and (PDF_DIR / f"{item['pmid']}.pdf").exists())
    ]
    logger.info(f"过滤已下载后待重试 {len(pending)} 篇。")

    if not pending:
        _clear_pdf_checkpoint()
        logger.info("所有待重试项均已存在，无需下载。")
        return

    # 混合取链：已有链接的直接用；链接缺失的重新查 OA API
    missing = [item for item in pending if not item.get("links")]
    if missing:
        missing_pmcs = [item["pmc_id"] for item in missing if item.get("pmc_id")]
        if missing_pmcs:
            logger.info(f"需重新查询 OA 链接 {len(missing_pmcs)} 篇...")
            oa_links, _ = fetch_oa_links(missing_pmcs)
            for item in pending:
                if not item.get("links"):
                    item["links"] = oa_links.get(item.get("pmc_id"), {})

    success_count = 0
    failed_items: list[dict] = []
    remaining = list(pending)

    with ThreadPoolExecutor(max_workers=DOWNLOAD_MAX_WORKERS) as executor:
        future_to_item: dict = {}
        for item in pending:
            pmid = item["pmid"]
            pdf_path = PDF_DIR / f"{pmid}.pdf"
            future = executor.submit(download_oa_pdf, item.get("links") or {}, pdf_path)
            future_to_item[future] = item

        for future in as_completed(future_to_item):
            item = future_to_item[future]
            if future.result():
                success_count += 1
                remaining.remove(item)
            else:
                failed_items.append(item)

            if success_count > 0 and success_count % RETRY_CHECKPOINT_INTERVAL == 0:
                _save_pdf_checkpoint(remaining)

    logger.info(f"重试完成：成功 {success_count} 篇，失败 {len(failed_items)} 篇。")

    if failed_items:
        _save_pdf_checkpoint(failed_items)
        export_failed_links_csv(failed_items)
        logger.info(f"仍有 {len(failed_items)} 篇失败，检查点已保留，可再次运行 --step pdf-retry。")
    else:
        _clear_pdf_checkpoint()
        logger.info("全部重试成功。")
