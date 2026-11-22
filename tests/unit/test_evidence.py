"""分析证据原语与跨模块私有导入边界。"""

import ast
import hashlib
from pathlib import Path

from src.analysis.evidence import digest, fingerprints, identity, records
from src.analysis.r_values import r_value


def test_digest_and_identity_match_bytes() -> None:
    """身份摘要与字节长度来自同一份输入。"""
    data = b"abc"

    assert digest(data) == hashlib.sha256(data).hexdigest()
    assert identity(data) == {"bytes": 3, "sha256": hashlib.sha256(data).hexdigest()}


def test_fingerprints_preserve_order_and_record_resolved_path(tmp_path: Path) -> None:
    """指纹按输入顺序返回，路径解析为绝对路径。"""
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"a")
    second.write_bytes(b"bb")

    result = fingerprints([second, first])

    assert [record["path"] for record in result] == [
        str(second.resolve()),
        str(first.resolve()),
    ]
    assert [record["bytes"] for record in result] == [2, 1]
    assert [record["sha256"] for record in result] == [
        hashlib.sha256(b"bb").hexdigest(),
        hashlib.sha256(b"a").hexdigest(),
    ]


def test_records_reads_utf8_bom_and_keeps_order() -> None:
    """内部 CSV 允许 UTF-8 BOM，并按原始行顺序返回。"""
    data = "\ufeffbarcode,keep\nb2,0\nb1,1\n".encode()

    assert records(data) == [{"barcode": "b2", "keep": "0"}, {"barcode": "b1", "keep": "1"}]


def test_records_handles_empty_csv() -> None:
    """没有表头时返回空列表。"""
    assert records(b"") == []


def test_r_value_serializes_protocol_values() -> None:
    """R 字面量覆盖 None、布尔、嵌套列表与 JSON 标量。"""
    assert r_value(None) == "NULL"
    assert r_value(True) == "TRUE"
    assert r_value(False) == "FALSE"
    assert r_value([1, "x", None, [True]]) == 'c(1,"x",NULL,c(TRUE))'
    assert r_value({"a": 1}) == '{"a": 1}'


def test_no_private_import_between_analysis_or_data_modules() -> None:
    """分析/数据模块不得再从兄弟模块导入下划线私有名。"""
    root = Path(__file__).resolve().parents[2]
    sources = list((root / "src").rglob("*.py"))
    assert len(sources) > 50
    violations = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not (
                node.module in ("src.analysis", "src.data")
                or node.module.startswith(("src.analysis.", "src.data."))
            ):
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    violations.append(f"{path.relative_to(root)}: {node.module}.{alias.name}")

    assert violations == []
