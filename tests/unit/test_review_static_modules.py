"""复核页静态模块边界：渲染层不承担 HTTP 与领域推导，API 层不碰 DOM。"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "public_api" / "static"


def _scan(path: Path, checks: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    """返回逐行命中的违规描述，带文件名与行号。"""
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for label, pattern in checks:
            if pattern.search(line):
                hits.append(f"{path.name}:{lineno}: {label}: {line.strip()}")
    return hits


def test_review_render_module_contract() -> None:
    """review.js 只做渲染：不发请求，不重复定义模型函数，不按动作 id 后缀推导。"""
    path = STATIC / "review.js"
    violations = _scan(
        path,
        [
            ("渲染层不得直接发起 HTTP 请求", re.compile(r"\bfetch\s*\(")),
            (
                "渲染层不得本地实现纯模型函数",
                re.compile(
                    r"\bfunction\s+(?:reviewerRole|noticeFor|actionKind)\b"
                    r"|(?:const|let|var)\s+(?:reviewerRole|noticeFor|actionKind)\s*="
                ),
            ),
            ("渲染层不得引用已删除的角色推导", re.compile(r"\breviewerRole\b")),
            (
                "渲染层不得用动作 id 的 _pass/_needs_changes/_reject 后缀判断类型",
                re.compile(r"_(?:pass|needs_changes|reject)\b"),
            ),
        ],
    )
    assert not violations, "review.js 边界违规：\n" + "\n".join(violations)


def test_review_render_imports_modules() -> None:
    """review.js 必须复用拆分后的 API、模型与选择快照模块。"""
    text = (STATIC / "review.js").read_text(encoding="utf-8")
    for module in ("review-api.js", "review-model.js", "evidence-selection.js"):
        assert re.search(rf"""from\s+["']\./{re.escape(module)}["']""", text), (
            f"review.js 缺少对 {module} 的 import"
        )


def test_review_reads_selection_snapshot() -> None:
    """详情与提交只读选择快照，不再缓存独立的 selected/events/review。"""
    text = (STATIC / "review.js").read_text(encoding="utf-8")
    for token in ("state.selected", "state.events", "state.review"):
        assert not re.search(rf"\b{re.escape(token)}\b", text), (
            f"review.js 仍直接读取 {token}"
        )
    assert re.search(r"selection\.current\(\)\?\.id", text), (
        "review.js 的提交目标必须取 selection.current()?.id"
    )


def test_review_api_has_no_dom() -> None:
    """review-api.js 只封装 HTTP，不读写 document/window。"""
    path = STATIC / "review-api.js"
    violations = _scan(
        path,
        [
            ("API 层不得操作 DOM", re.compile(r"\b(?:document|window)\s*[.\[]")),
        ],
    )
    assert not violations, "review-api.js 边界违规：\n" + "\n".join(violations)


def test_review_model_has_no_dom_or_fetch() -> None:
    """review-model.js 保持纯函数，不碰 DOM 也不发请求。"""
    path = STATIC / "review-model.js"
    violations = _scan(
        path,
        [
            ("模型层不得发起 HTTP 请求", re.compile(r"\bfetch\s*\(")),
            ("模型层不得操作 DOM", re.compile(r"\b(?:document|window)\s*[.\[]")),
        ],
    )
    assert not violations, "review-model.js 边界违规：\n" + "\n".join(violations)
