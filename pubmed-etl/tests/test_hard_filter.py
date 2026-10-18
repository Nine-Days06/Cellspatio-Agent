"""hard_filter 回归测试：单条规则判定、重复标题检测与 filter_log 落库。

用例全部使用临时 SQLite（tempfile 建库），绝不读写 data/processed/multiomics_lit.db。
过滤阈值一律显式 patch 掉 config.settings 的实际取值，避免用例随配置调整而失效。
"""

import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cleaner import hard_filter
from cleaner.hard_filter import (
    check_article_type,
    find_duplicate_titles,
    run_hard_filter,
)
from utils.db import get_conn, init_db

# ── 测试用固定阈值（覆盖 config.settings 的真实取值）────────────────
EXCLUDED_ARTICLE_TYPES = ("Letter", "Comment", "Editorial")

LONG_ABSTRACT = (
    "Single-cell RNA sequencing of human tumor microenvironment reveals "
    "distinct malignant epithelial states and exhausted T cell niches "
    "across twelve patient donors."
)

# articles 表列顺序与 utils/db.py 的 CREATE_ARTICLES_SQL 保持一致
ARTICLE_COLUMNS = (
    "pmid",
    "title",
    "abstract",
    "keywords",
    "mesh_terms",
    "pub_year",
    "pub_month",
    "journal",
    "journal_abbr",
    "doi",
    "pmc_id",
    "article_types",
    "authors",
    "affiliation",
    "language",
    "raw_xml_file",
)
INSERT_SQL = (
    "INSERT INTO articles ("
    + ", ".join(ARTICLE_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" * len(ARTICLE_COLUMNS))
    + ")"
)


def make_row(**overrides) -> dict:
    """构造一条默认合规的 article 记录；overrides 覆盖任意列"""
    row = {
        "pmid": "40000001",
        "title": "Single-cell RNA sequencing of human tumor microenvironment",
        "abstract": LONG_ABSTRACT,
        "keywords": "single-cell|human|tumor",
        "mesh_terms": "Single-Cell Analysis",
        "pub_year": 2020,
        "pub_month": "05",
        "journal": "Nature",
        "journal_abbr": "Nature",
        "doi": "10.1038/s41586-020-00001",
        "pmc_id": "PMC7000001",
        "article_types": "Journal Article",
        "authors": "Smith John|Jane Doe",
        "affiliation": "Department of Biology",
        "language": "eng",
        "raw_xml_file": "batch_test_001.xml",
    }
    row.update(overrides)
    return row


class _TempDbTestCase(unittest.TestCase):
    """临时 SQLite 基类：每个用例独立建库，用例结束自动清理"""

    def setUp(self):
        tmp_dir = tempfile.TemporaryDirectory()
        # init_db() 用的是 `with sqlite3.connect(...)`——那是事务上下文，不会关闭连接，
        # 连接对象又落在引用环里只能靠 gc 回收。Windows 上文件被占用会导致删不掉临时目录，
        # 故先 gc.collect() 再清理（LIFO：后注册的先执行）。
        self.addCleanup(tmp_dir.cleanup)
        self.addCleanup(gc.collect)
        self.db_path = Path(tmp_dir.name) / "test_lit.db"
        init_db(self.db_path)

    def _insert(self, **overrides) -> str:
        """插入一条 article 记录（默认合规），返回 pmid"""
        row = make_row(**overrides)
        with get_conn(self.db_path) as conn:
            conn.execute(INSERT_SQL, tuple(row[column] for column in ARTICLE_COLUMNS))
        return row["pmid"]

    def _fetch(self, pmid: str) -> sqlite3.Row:
        """取回一行 sqlite3.Row（与生产代码的 row_factory 约定一致）"""
        with get_conn(self.db_path) as conn:
            return conn.execute("SELECT * FROM articles WHERE pmid = ?", (pmid,)).fetchone()

    def _filter_log(self) -> dict:
        """返回 {pmid: reason}，仅限 hard_filter 阶段"""
        with get_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT pmid, reason FROM filter_log WHERE stage = 'hard_filter'"
            ).fetchall()
        return {row["pmid"]: row["reason"] for row in rows}


class TestCheckArticleType(_TempDbTestCase):
    """文章类型规则：命中排除列表即过滤，大小写不敏感"""

    @patch.object(hard_filter, "EXCLUDED_ARTICLE_TYPES", EXCLUDED_ARTICLE_TYPES)
    def test_journal_article_passes(self):
        row = self._fetch(self._insert(pmid="40000401", article_types="Journal Article"))
        self.assertIsNone(check_article_type(row))

    @patch.object(hard_filter, "EXCLUDED_ARTICLE_TYPES", EXCLUDED_ARTICLE_TYPES)
    def test_excluded_type_is_reported(self):
        row = self._fetch(self._insert(pmid="40000402", article_types="Letter"))
        self.assertEqual(check_article_type(row), "excluded_type(Letter)")

    @patch.object(hard_filter, "EXCLUDED_ARTICLE_TYPES", EXCLUDED_ARTICLE_TYPES)
    def test_excluded_type_among_several_is_detected(self):
        """排除类型混在多类型列表中（'|' 分隔）同样应被拦下"""
        row = self._fetch(self._insert(pmid="40000403", article_types="Journal Article|Editorial"))
        self.assertEqual(check_article_type(row), "excluded_type(Editorial)")

    @patch.object(hard_filter, "EXCLUDED_ARTICLE_TYPES", EXCLUDED_ARTICLE_TYPES)
    def test_matching_is_case_insensitive(self):
        row = self._fetch(self._insert(pmid="40000404", article_types="journal article|comment"))
        self.assertEqual(check_article_type(row), "excluded_type(Comment)")

    @patch.object(hard_filter, "EXCLUDED_ARTICLE_TYPES", EXCLUDED_ARTICLE_TYPES)
    def test_empty_types_passes(self):
        row = self._fetch(self._insert(pmid="40000405", article_types=""))
        self.assertIsNone(check_article_type(row))


class TestFindDuplicateTitles(_TempDbTestCase):
    """去重：同期刊同年份同标题（小写规范化后）只保留最早一条"""

    def test_same_title_journal_year_marks_later_pmid(self):
        first = self._insert(pmid="40000601", journal="Nature", pub_year=2020)
        second = self._insert(pmid="40000602", journal="Nature", pub_year=2020)
        self.assertEqual(find_duplicate_titles(self.db_path), {second})
        self.assertNotIn(first, find_duplicate_titles(self.db_path))

    def test_same_title_different_journal_is_not_duplicate(self):
        self._insert(pmid="40000603", journal="Nature", pub_year=2020)
        other = self._insert(pmid="40000604", journal="Cell", pub_year=2020)
        self.assertEqual(find_duplicate_titles(self.db_path), set())
        self.assertNotIn(other, find_duplicate_titles(self.db_path))

    def test_same_title_different_year_is_not_duplicate(self):
        self._insert(pmid="40000605", journal="Nature", pub_year=2020)
        other = self._insert(pmid="40000606", journal="Nature", pub_year=2021)
        self.assertNotIn(other, find_duplicate_titles(self.db_path))

    def test_comparison_ignores_case_and_surrounding_spaces(self):
        self._insert(pmid="40000607", title="Spatial transcriptomics of human liver")
        other = self._insert(pmid="40000608", title="  SPATIAL TRANSCRIPTOMICS OF HUMAN LIVER  ")
        self.assertIn(other, find_duplicate_titles(self.db_path))

    def test_rows_without_title_are_skipped(self):
        """无标题记录不参与去重，也不应抛异常"""
        self._insert(pmid="40000609", title="")
        self._insert(pmid="40000610", title=None)
        self.assertEqual(find_duplicate_titles(self.db_path), set())

    def test_accepts_external_connection(self):
        """复用调用方连接时结果应与自开连接一致"""
        first = self._insert(pmid="40000611")
        second = self._insert(pmid="40000612")
        with get_conn(self.db_path) as conn:
            self.assertEqual(find_duplicate_titles(self.db_path, conn=conn), {second})
        self.assertEqual(find_duplicate_titles(self.db_path), {second})
        self.assertNotIn(first, find_duplicate_titles(self.db_path))


@patch.object(hard_filter, "EXCLUDED_ARTICLE_TYPES", EXCLUDED_ARTICLE_TYPES)
class TestRunHardFilter(_TempDbTestCase):
    """主流程：规则命中写入 filter_log，未命中即通过"""

    def _run(self) -> dict:
        return run_hard_filter(db_path=self.db_path)

    def test_compliant_article_passes(self):
        self._insert(pmid="40000701")
        stats = self._run()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["filtered"], 0)
        self.assertEqual(stats["passed"], 1)
        self.assertEqual(stats["reason_counts"], {})
        self.assertEqual(self._filter_log(), {})

    def test_excluded_article_type_is_filtered(self):
        pmid = self._insert(pmid="40000704", article_types="Journal Article|Letter")
        stats = self._run()
        self.assertEqual(stats["filtered"], 1)
        self.assertEqual(stats["reason_counts"]["excluded_type"], 1)
        self.assertEqual(self._filter_log()[pmid], "excluded_type(Letter)")

    def test_duplicate_title_keeps_first_and_filters_rest(self):
        first = self._insert(pmid="40000709", journal="Nature", pub_year=2020)
        second = self._insert(pmid="40000710", journal="Nature", pub_year=2020)
        third = self._insert(pmid="40000711", journal="Nature", pub_year=2020)
        stats = self._run()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["filtered"], 2)
        self.assertEqual(stats["passed"], 1)
        self.assertEqual(stats["reason_counts"]["duplicate_title"], 2)
        # 去重优先于其余规则：首条保留，后两条一律记为 duplicate_title
        self.assertEqual(set(self._filter_log()), {second, third})
        # 查询 articles 表中不在 filter_log 里的记录（即通过过滤的记录）
        with get_conn(self.db_path) as conn:
            passed_pmids = [
                r["pmid"]
                for r in conn.execute(
                    "SELECT pmid FROM articles WHERE NOT EXISTS ("
                    "SELECT 1 FROM filter_log WHERE pmid = articles.pmid AND stage = 'hard_filter')"
                ).fetchall()
            ]
        self.assertEqual(passed_pmids, [first])

    def test_duplicate_title_takes_priority_over_other_rules(self):
        """重复标题本身已足够过滤，不应被后续规则覆盖原因"""
        self._insert(pmid="40000712", journal="Cell", pub_year=2020, abstract="")
        second = self._insert(pmid="40000713", journal="Cell", pub_year=2020, abstract="")
        self._run()
        self.assertEqual(self._filter_log()[second], "duplicate_title")

    def test_each_article_reports_exactly_one_reason(self):
        """一条记录只记一次原因，首个命中规则即短路"""
        self._insert(pmid="40000714", article_types="Letter")
        self._run()
        self.assertEqual(len(self._filter_log()), 1)

    def test_mixed_batch_splits_into_passed_and_filtered(self):
        self._insert(pmid="40000715")
        self._insert(pmid="40000716", article_types="Journal Article|Editorial")
        self._insert(pmid="40000717", article_types="Journal Article|Letter")
        stats = self._run()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["filtered"], 2)
        self.assertEqual(stats["passed"], 1)
        self.assertEqual(stats["passed"], stats["total"] - stats["filtered"])
        self.assertEqual(len(self._filter_log()), stats["filtered"])

    def test_filter_log_records_hard_filter_stage(self):
        pmid = self._insert(pmid="40000718", article_types="Journal Article|Letter")
        self._run()
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT stage, filtered_at FROM filter_log WHERE pmid = ?", (pmid,)
            ).fetchone()
        self.assertEqual(row["stage"], "hard_filter")
        self.assertTrue(row["filtered_at"])

    def test_rerun_is_idempotent(self):
        """重复运行不得叠加日志，且统计结果保持一致"""
        self._insert(pmid="40000719")
        self._insert(pmid="40000720", article_types="Journal Article|Letter")
        first = self._run()
        second = self._run()
        self.assertEqual(first, second)
        self.assertEqual(len(self._filter_log()), 1)

    def test_rerun_clears_logs_of_articles_that_now_pass(self):
        """上一轮被过滤、本轮已合规的记录，其历史日志必须被清除"""
        pmid = self._insert(pmid="40000721", journal="Cell")
        self._insert(pmid="40000722", journal="Cell")  # 与上一条同标题同刊同年 → 重复
        self._run()
        self.assertIn("40000722", self._filter_log())

        with get_conn(self.db_path) as conn:
            conn.execute(
                "UPDATE articles SET title = 'A Completely Different Title Here' WHERE pmid = ?",
                (pmid,),
            )
        self._run()
        self.assertEqual(self._filter_log(), {})

    def test_logs_of_other_stages_are_preserved(self):
        """清理只针对 hard_filter 阶段，不得误删其他阶段的日志"""
        self._insert(pmid="40000723")
        with get_conn(self.db_path) as conn:
            conn.execute(
                "INSERT INTO filter_log (pmid, stage, reason, filtered_at) "
                "VALUES (?, 'relevance', 'llm_not_relevant', '2024-01-01T00:00:00')",
                ("40000723",),
            )
        self._run()
        with get_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT reason FROM filter_log WHERE pmid = ? AND stage = 'relevance'",
                ("40000723",),
            ).fetchone()
        self.assertEqual(row["reason"], "llm_not_relevant")

    def test_empty_articles_table_yields_zero_stats(self):
        stats = self._run()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["filtered"], 0)
        self.assertEqual(stats["passed"], 0)
        self.assertEqual(self._filter_log(), {})


if __name__ == "__main__":
    unittest.main()
