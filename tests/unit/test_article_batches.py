"""文献批次交接契约测试。"""

import ast
from pathlib import Path

import pytest

from src.article_batches import (
    EXPORT_FIELDS,
    ArticleBatchError,
    discover_batches,
    latest_batch,
    validate_header,
)

_LLM_VALIDATOR = Path(__file__).resolve().parents[2] / "pubmed-etl" / "cleaner" / "llm_validator.py"


def test_discover_and_latest_exclude_raw(tmp_path):
    """松散 glob 会误取 raw；批次发现只认契约名。"""
    for name in (
        "articles_20260921_100000.csv",
        "articles_20260921_110000.csv",
        "articles_raw_20260921_120000.csv",
    ):
        (tmp_path / name).write_text("pmid\n", encoding="utf-8")

    assert [p.name for p in discover_batches(tmp_path)] == [
        "articles_20260921_100000.csv",
        "articles_20260921_110000.csv",
    ]
    assert latest_batch(tmp_path).name == "articles_20260921_110000.csv"


def test_validate_header_reports_missing_columns(tmp_path):
    """缺少契约列时在导入前失败。"""
    path = tmp_path / "articles_20260921_100000.csv"
    path.write_text("pmid,title\n1,T\n", encoding="utf-8")

    with pytest.raises(ArticleBatchError, match="abstract"):
        validate_header(path, required=EXPORT_FIELDS)


def test_producer_fields_and_name_match_contract():
    """ETL 生产侧的文件名与 9 列字段必须与消费侧一致。"""
    source = _LLM_VALIDATOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fields = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ARTICLES_EXPORT_FIELDS"
            for target in node.targets
        ):
            fields = tuple(ast.literal_eval(node.value))
    assert fields == EXPORT_FIELDS
    export_fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_export_articles_csv"
    )
    export_source = ast.get_source_segment(source, export_fn)
    assert export_source is not None
    assert "%Y%m%d_%H%M%S" in export_source
    assert 'f"articles_{ts}.csv"' in export_source
