"""硬过滤模块 - 对文献进行基础质量过滤"""
import sqlite3
from pathlib import Path
from datetime import datetime

from config.settings import (
    DB_PATH,
    ABSTRACT_MIN_LEN,
    PUB_YEAR_MIN, PUB_YEAR_MAX,
    EXCLUDED_ARTICLE_TYPES,
)
from utils.db import get_conn, now_iso

INSERT_LOG_SQL = """
INSERT OR REPLACE INTO filter_log (pmid, stage, reason, filtered_at)
VALUES (?, 'hard_filter', ?, ?)
"""


# ── 单条规则函数 ──────────────────────────────────────────────

def check_language(row: sqlite3.Row) -> str | None:
    """非英文返回原因"""
    lang = (row["language"] or "").lower().strip()
    if lang and lang != "eng":
        return f"language={lang}"
    return None


def check_abstract(row: sqlite3.Row) -> str | None:
    """摘要为空或过短"""
    abstract = (row["abstract"] or "").strip()
    if not abstract:
        return "abstract_empty"
    if len(abstract) < ABSTRACT_MIN_LEN:
        return f"abstract_too_short({len(abstract)}chars)"
    return None


def check_year(row: sqlite3.Row) -> str | None:
    """年份超出范围"""
    year = row["pub_year"]
    if year is None:
        return "pub_year_missing"
    if year < PUB_YEAR_MIN:
        return f"pub_year_too_old({year})"
    if year > PUB_YEAR_MAX:
        return f"pub_year_future({year})"
    return None


def check_article_type(row: sqlite3.Row) -> str | None:
    """文章类型在排除列表中"""
    types = (row["article_types"] or "").lower()
    for excl in EXCLUDED_ARTICLE_TYPES:
        if excl.lower() in types:
            return f"excluded_type({excl})"
    return None


def check_title(row: sqlite3.Row) -> str | None:
    """标题为空或过短"""
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
    找出同期刊、同年份、完全相同标题的重复 PMID。
    保留最小 PMID，其余标记为重复。
    """
    query = """
    SELECT pmid, LOWER(TRIM(title)) AS norm_title, journal, pub_year
    FROM articles
    WHERE title IS NOT NULL AND title != ''
    """
    duplicates: set[str] = set()
    seen: dict[tuple, str] = {}

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
    """对 articles 表全量扫描，将不符合条件的记录写入 filter_log"""
    db_path = Path(db_path)

    print("=" * 60)
    print("阶段三：硬过滤")
    print("=" * 60)

    with get_conn(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        print(f"  articles 表共 {total} 条记录")

        # 检测重复标题
        print("  检测重复标题 ...")
        dup_pmids = find_duplicate_titles(db_path, conn=conn)
        print(f"  发现重复标题 {len(dup_pmids)} 篇")

        reason_counts: dict[str, int] = {}
        filtered_pmids: set[str] = set()
        log_rows: list[tuple] = []

        # 清理旧的过滤日志
        conn.execute("DELETE FROM filter_log WHERE stage = 'hard_filter'")

        # 分批读取
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
                print(f"    已扫描 {offset} / {total} ...")

        # 批量写入过滤日志
        conn.executemany(INSERT_LOG_SQL, log_rows)

    passed = total - len(filtered_pmids)
    print(f"  硬过滤完成：保留 {passed} 篇，过滤 {len(filtered_pmids)} 篇")
    print("  过滤原因统计：")
    for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    {reason:<40} {cnt:>6} 篇")

    return {
        "total": total,
        "filtered": len(filtered_pmids),
        "passed": passed,
        "reason_counts": reason_counts,
    }


def get_passed_pmids(db_path: Path = DB_PATH, conn: sqlite3.Connection = None) -> list[str]:
    """返回通过硬过滤的 PMID 列表"""
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
