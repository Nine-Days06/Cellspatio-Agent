# utils/db.py
"""SQLite 数据库工具函数"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager


# 建表 DDL
CREATE_ARTICLES_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    pmid          TEXT PRIMARY KEY,
    title         TEXT,
    abstract      TEXT,
    keywords      TEXT,       -- '|' 分隔
    mesh_terms    TEXT,       -- '|' 分隔
    pub_year      INTEGER,
    pub_month     TEXT,
    journal       TEXT,
    journal_abbr  TEXT,
    doi           TEXT,
    pmc_id        TEXT,       -- PMC 编号，有则可尝试获取全文
    article_types TEXT,       -- '|' 分隔，来自 PublicationTypeList
    authors       TEXT,       -- '|' 分隔，"LastName FirstName" 格式
    affiliation   TEXT,       -- 第一作者单位
    language      TEXT,
    raw_xml_file  TEXT        -- 来源 XML 批次文件名，便于溯源
);
"""

CREATE_FILTER_LOG_SQL = """
CREATE TABLE IF NOT EXISTS filter_log (
    pmid          TEXT PRIMARY KEY,
    stage         TEXT,       -- 'hard_filter' / 'relevance'
    reason        TEXT,       -- 被过滤的原因
    filtered_at   TEXT        -- ISO 时间戳
);
"""

CREATE_LLM_VALIDATION_SQL = """
CREATE TABLE IF NOT EXISTS llm_validation (
    pmid             TEXT PRIMARY KEY,
    llm_verdict      TEXT,      -- RELEVANT / NOT_RELEVANT
    reason           TEXT,      -- LLM 判断理由
    validated_at     TEXT,      -- 验证时间
    human_review     TEXT       -- NULL=待复核, Y=通过, N=驳回
);
"""


def init_db(db_path: Path) -> None:
    """初始化数据库，创建所有表"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(CREATE_ARTICLES_SQL)
        conn.execute(CREATE_FILTER_LOG_SQL)
        conn.execute(CREATE_LLM_VALIDATION_SQL)
        conn.commit()


@contextmanager
def get_conn(db_path: Path):
    """提供数据库连接上下文，自动 commit/rollback"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


VALID_TABLES = {"articles", "filter_log", "llm_validation"}


def get_all_pmids(db_path: Path) -> list[str]:
    """返回数据库中所有 PMID"""
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT pmid FROM articles").fetchall()
    return [r["pmid"] for r in rows]


def count_table(db_path: Path, table: str) -> int:
    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table name: {table}")
    with get_conn(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
