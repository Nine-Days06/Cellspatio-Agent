"""convert_article_to_text：文献文本渲染（含基因行注入）。"""

from src.knowledge.article_text import convert_article_to_text


def test_convert_includes_gene_line_when_genes_given():
    """传入 genes 时追加一行规范 token（位置在文末，不影响既有字段）。"""
    article = {"pmid": "9704485", "title": "T", "abstract": "A"}
    text = convert_article_to_text(article, genes=["NCBIGene:947", "NCBIGene:952"])
    assert "基因ID：NCBIGene:947, NCBIGene:952" in text


def test_convert_omits_gene_line_without_genes():
    """无 genes（默认/空列表）不产生该行。"""
    article = {"pmid": "9704485", "title": "T", "abstract": "A"}
    assert "基因ID：" not in convert_article_to_text(article)
    assert "基因ID：" not in convert_article_to_text(article, genes=[])
