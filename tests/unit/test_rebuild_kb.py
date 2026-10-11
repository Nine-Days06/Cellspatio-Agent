"""rebuild_kb 清库守卫单测：清空 knowledge_base 前必须先校验文档来源。

场景覆盖：
1. 空库/无库 → 放行
2. 纯公共数据库（20 条来源）文档 → 放行并清空（保留 EMBEDDING_MODEL.json）
3. 含文献等非公共数据库文档 → 拒绝（SystemExit 2）且文档原样保留
4. 显式 --force → 放行并清空
5. kv_store_full_docs.json 损坏 → 拒绝（fail-closed）且文件保留
"""

import json

import pytest

import rebuild_kb


def _write_docs(kb_dir, docs: dict) -> None:
    """写入 kv_store_full_docs.json"""
    (kb_dir / rebuild_kb.DOCS_FILE).write_text(
        json.dumps(docs, ensure_ascii=False), encoding="utf-8"
    )


def test_empty_kb_pass(monkeypatch, tmp_path):
    """无库：find_foreign_docs 返回空，clear_kb 直接放行"""
    monkeypatch.setattr(rebuild_kb, "KB_DIR", tmp_path / "knowledge_base")

    assert rebuild_kb.find_foreign_docs() == []
    rebuild_kb.clear_kb()  # 不抛异常即通过
    assert (tmp_path / "knowledge_base").is_dir()


def test_curated_only_pass_and_clear(monkeypatch, tmp_path):
    """纯公共库：放行清空，保留 EMBEDDING_MODEL.json"""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    monkeypatch.setattr(rebuild_kb, "KB_DIR", kb)
    _write_docs(kb, {
        "doc1": {"content": "# KEGG 通路: map04110\nENTRY map04110"},
        "doc2": {"content": "# UniProt 蛋白: P04637\n基因：TP53"},
        "doc3": {"content": "# GEO 数据集: GSE123456\n标题：..."},
    })
    (kb / "vdb_constants.json").write_text("{}", encoding="utf-8")
    (kb / "EMBEDDING_MODEL.json").write_text("{}", encoding="utf-8")

    assert rebuild_kb.find_foreign_docs() == []
    rebuild_kb.clear_kb()
    assert not (kb / "vdb_constants.json").exists()
    assert (kb / "EMBEDDING_MODEL.json").exists()
    assert not (kb / rebuild_kb.DOCS_FILE).exists()  # 连同公共库文档一并清空


def test_literature_reject_and_keep(monkeypatch, tmp_path):
    """含文献：拒绝（SystemExit 2），文档原样保留"""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    monkeypatch.setattr(rebuild_kb, "KB_DIR", kb)
    _write_docs(kb, {
        "pub_30872783": {"content": "标题：xxx\nhttps://pubmed.ncbi.nlm.nih.gov/30872783/"},
        "doc_kegg": {"content": "# KEGG 通路: map04110"},
    })

    foreign = rebuild_kb.find_foreign_docs()
    assert foreign == ["pub_30872783"]

    with pytest.raises(SystemExit) as exc:
        rebuild_kb.clear_kb()
    assert exc.value.code == 2
    assert (kb / rebuild_kb.DOCS_FILE).exists()  # 文件未被删除


def test_force_clears_literature(monkeypatch, tmp_path):
    """--force：显式放行并清空"""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    monkeypatch.setattr(rebuild_kb, "KB_DIR", kb)
    _write_docs(kb, {"pub_30872783": {"content": "文献正文 PMID 30872783"}})

    rebuild_kb.clear_kb(force=True)
    assert not (kb / rebuild_kb.DOCS_FILE).exists()


def test_corrupted_docs_reject_fail_closed(monkeypatch, tmp_path):
    """kv_store_full_docs.json 损坏：无法校验来源 → 拒绝清库（fail-closed）"""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    monkeypatch.setattr(rebuild_kb, "KB_DIR", kb)
    corrupted = kb / rebuild_kb.DOCS_FILE
    corrupted.write_text("{invalid json !!", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        rebuild_kb.clear_kb()
    assert exc.value.code == 2
    assert corrupted.exists()  # 损坏文件原样保留，未被误删


def test_curated_headers_cover_new_sources():
    """新 curated 文档头必须登记，否则 find_foreign_docs 会把它们误判为文献"""
    assert "# CellMarker 细胞类型:" in rebuild_kb.CURATED_HEADERS
    assert "# Cell Ontology 术语:" in rebuild_kb.CURATED_HEADERS


def test_new_curated_docs_pass_guard(monkeypatch, tmp_path):
    """CellMarker / Cell Ontology 文档能通过清库守卫"""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    monkeypatch.setattr(rebuild_kb, "KB_DIR", kb)
    _write_docs(kb, {
        "cm1": {"content": "# CellMarker 细胞类型:\n## B cell"},
        "cl1": {"content": "# Cell Ontology 术语:\nCL:0000084 T cell"},
    })
    assert rebuild_kb.find_foreign_docs() == []
    rebuild_kb.clear_kb()
    assert not (kb / rebuild_kb.DOCS_FILE).exists()


def test_all_ok_requires_every_source():
    """逐源校验：每源 requested > 0 且 inserted == requested 才算全部成功。"""
    assert rebuild_kb._all_sources_fully_inserted([
        ("kegg", {"requested": 8, "inserted": 8}),
        ("cellmarker", {"requested": 170, "inserted": 170}),
    ])
    # 某一源 inserted < requested → 失败
    assert not rebuild_kb._all_sources_fully_inserted([
        ("kegg", {"requested": 8, "inserted": 8}),
        ("geo", {"requested": 2, "inserted": 1}),
    ])
    # 某一源 requested == 0 → 失败
    assert not rebuild_kb._all_sources_fully_inserted([
        ("cell_ontology", {"requested": 0, "inserted": 0}),
    ])


def test_curated_headers_cover_push3_sources():
    """推进顺序 3 的新文档头必须登记，否则清库守卫误判为文献"""
    assert "# KEGG 通路结构:" in rebuild_kb.CURATED_HEADERS
    assert "# Reactome 通路:" in rebuild_kb.CURATED_HEADERS
    assert "# HPA 蛋白:" in rebuild_kb.CURATED_HEADERS


def test_push3_docs_pass_guard(monkeypatch, tmp_path):
    """KGML/Reactome/HPA 文档能通过清库守卫"""
    kb = tmp_path / "knowledge_base"
    kb.mkdir()
    monkeypatch.setattr(rebuild_kb, "KB_DIR", kb)
    _write_docs(kb, {
        "kg1": {"content": "# KEGG 通路结构: hsa04110\n名称：Cell cycle"},
        "re1": {"content": "# Reactome 通路: R-HSA-1640170\n名称：Cell Cycle"},
        "hp1": {"content": "# HPA 蛋白: CD4\nEnsembl：ENSG00000010610"},
    })
    assert rebuild_kb.find_foreign_docs() == []
    rebuild_kb.clear_kb()
    assert not (kb / rebuild_kb.DOCS_FILE).exists()


def test_kegg_plain_header_not_substring_of_kgml():
    """`# KEGG 通路:` 不是 `# KEGG 通路结构:` 的子串 → 两个头都要登记（逐字断言）"""
    assert "# KEGG 通路:" in rebuild_kb.CURATED_HEADERS
    assert "# KEGG 通路结构:" in rebuild_kb.CURATED_HEADERS
    assert "# KEGG 通路:" not in "# KEGG 通路结构: hsa04110"  # noqa: PLR0133 - 字面量子串关系是本测试的语义
