# cleaner/hard_filter.py
"""
硬过滤模块
逐条检查数据库中的文献，对不符合条件的记录打上过滤标记（写入 filter_log 表）。
过滤规则（任一满足即过滤）：
  1. 语言不是英文（language != 'eng'）
  2. 摘要为空或过短（< ABSTRACT_MIN_LEN 字符）
  3. 发表年份超出范围
  4. 文章类型属于排除列表（Letter / Comment / Correction 等）
  5. 标题为空
  6. 疑似重复标题（同期刊同年份完全相同的标题）
"""

import sqlite3
from pathlib import Path

from config.settings import (
    DB_PATH,
    ABSTRACT_MIN_LEN,
    PUB_YEAR_MIN, PUB_YEAR_MAX,
    EXCLUDED_ARTICLE_TYPES,
)
from utils import now_iso
from utils.db import get_conn
from utils.logger import get_logger

logger = get_logger("hard_filter")

# 写入过滤日志
INSERT_LOG_SQL = """
INSERT OR REPLACE INTO filter_log (pmid, stage, reason, filtered_at)
VALUES (?, 'hard_filter', ?, ?)
"""


# ── 单条规则函数 ──────────────────────────────────────────────

def check_language(row: sqlite3.Row) -> str | None:
    """非英文返回原因描述，否则 None"""
    lang = (row["language"] or "").lower().strip()
    # PubMed 英文记录标记为 'eng'；也接受空值（部分记录没有语言字段）
    if lang and lang != "eng":
        return f"language={lang}"
    return None


def check_abstract(row: sqlite3.Row) -> str | None:
    abstract = (row["abstract"] or "").strip()
    if not abstract:
        return "abstract_empty"
    if len(abstract) < ABSTRACT_MIN_LEN:
        return f"abstract_too_short({len(abstract)}chars)"
    return None


def check_year(row: sqlite3.Row) -> str | None:
    year = row["pub_year"]
    if year is None:
        return "pub_year_missing"
    if year < PUB_YEAR_MIN:
        return f"pub_year_too_old({year})"
    if year > PUB_YEAR_MAX:
        return f"pub_year_future({year})"
    return None


def check_article_type(row: sqlite3.Row) -> str | None:
    types = (row["article_types"] or "").lower()
    for excl in EXCLUDED_ARTICLE_TYPES:
        if excl.lower() in types:
            return f"excluded_type({excl})"
    return None


def check_title(row: sqlite3.Row) -> str | None:
    title = (row["title"] or "").strip()
    if not title or len(title) < 10:
        return "title_empty_or_too_short"
    return None


RULE_FUNCS = [
    check_language,
    check_abstract,
    check_year,
    check_article_type,
    check_title,
]


# ── 重复标题检测 ──────────────────────────────────────────────

def find_duplicate_titles(db_path: Path = DB_PATH, conn: sqlite3.Connection = None) -> set[str]:
    """
    找出同期刊、同年份、完全相同标题（小写规范化后）的重复 PMID。
    保留最小 PMID（最早收录），其余标记为重复。
    """
    query = """
    SELECT pmid, LOWER(TRIM(title)) AS norm_title, journal, pub_year
    FROM articles
    WHERE title IS NOT NULL AND title != ''
    """
    duplicates: set[str] = set()
    seen: dict[tuple, str] = {}     # (norm_title, journal, year) → first_pmid

    if conn is not None:
        rows = conn.execute(query).fetchall()
    else:
        with get_conn(db_path) as c:
            rows = c.execute(query).fetchall()

    for row in rows:
        key = (row["norm_title"], row["journal"] or "", row["pub_year"])
        if key in seen:
            duplicates.add(row["pmid"])
        else:
            seen[key] = row["pmid"]

    return duplicates


# ── 主流程 ────────────────────────────────────────────────────

def run_hard_filter(db_path: Path = DB_PATH) -> dict:
    """
    对 articles 表全量扫描，将不符合条件的记录写入 filter_log。
    返回统计字典。
    """
    db_path = Path(db_path)
    logger.info("=" * 60)
    logger.info("阶段三-A：硬过滤")
    logger.info("=" * 60)

    with get_conn(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        logger.info(f"articles 表共 {total} 条记录")

        # 先找重复标题（复用同一连接）
        logger.info("检测重复标题 ...")
        dup_pmids = find_duplicate_titles(db_path, conn=conn)
        logger.info(f"发现重复标题 {len(dup_pmids)} 篇")

        reason_counts: dict[str, int] = {}
        filtered_pmids: set[str] = set()
        log_rows: list[tuple] = []

        # 清理旧的过滤日志（仅限当前阶段）
        conn.execute("DELETE FROM filter_log WHERE stage = 'hard_filter'")
        
        # 分批读取（避免全量加载到内存）
        page_size = 5000
        offset    = 0
        now       = now_iso()

        while True:
            rows = conn.execute(
                "SELECT * FROM articles LIMIT ? OFFSET ?", (page_size, offset)
            ).fetchall()

            if not rows:
                break

            for row in rows:
                pmid = row["pmid"]

                if pmid in dup_pmids:
                    reason = "duplicate_title"
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                    filtered_pmids.add(pmid)
                    log_rows.append((pmid, reason, now))
                    continue

                for rule_fn in RULE_FUNCS:
                    reason = rule_fn(row)
                    if reason:
                        reason_key = reason.split("(")[0]
                        reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1
                        filtered_pmids.add(pmid)
                        log_rows.append((pmid, reason, now))
                        break

            offset += page_size
            if offset % 20000 == 0:
                logger.info(f"  已扫描 {offset} / {total} ...")

        # 批量写入过滤日志
        conn.executemany(INSERT_LOG_SQL, log_rows)

    passed = total - len(filtered_pmids)
    logger.info(f"硬过滤完成：保留 {passed} 篇，过滤 {len(filtered_pmids)} 篇")
    logger.info("过滤原因统计：")
    for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
        logger.info(f"  {reason:<40} {cnt:>6} 篇")

    return {
        "total": total,
        "filtered": len(filtered_pmids),
        "passed": passed,
        "reason_counts": reason_counts,
    }


def get_passed_pmids(db_path: Path = DB_PATH, conn: sqlite3.Connection = None) -> list[str]:
    """
    返回通过硬过滤的 PMID 列表
    （即 articles 表中不在 filter_log 里的记录）
    """
    query = """
        SELECT pmid FROM articles a
        WHERE NOT EXISTS (
            SELECT 1 FROM filter_log f
            WHERE f.pmid = a.pmid AND f.stage = 'hard_filter'
        )
    """
    if conn is not None:
        rows = conn.execute(query).fetchall()
    else:
        with get_conn(db_path) as c:
            rows = c.execute(query).fetchall()
    return [r["pmid"] for r in rows]
