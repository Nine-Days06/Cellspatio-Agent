"""文章/文献文本转换工具"""

from typing import Any


def convert_article_to_text(article: dict[str, Any], *, genes: dict[str, str] | None = None) -> str:
    """将文章/文献转换为 LightRAG 可接受的文本格式

    genes: mention→CURIE 映射（如 {"CD34": "NCBIGene:947"}），非空时在文末追加
    `基因ID：` 块，逐行 `mention => CURIE`，供 LLM 建立规范基因实体。
    """
    text_parts = []

    if article.get("title"):
        text_parts.append(f"标题：{article['title']}")
    if article.get("abstract"):
        text_parts.append(f"摘要：{article['abstract']}")

    keywords = article.get("keywords", "")
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    elif isinstance(keywords, list):
        pass
    else:
        keywords = []
    if keywords:
        text_parts.append(f"关键词：{', '.join(keywords)}")

    mesh_terms = article.get("mesh_terms", "")
    if isinstance(mesh_terms, str):
        mesh_terms = [m.strip() for m in mesh_terms.split(",") if m.strip()]
    elif isinstance(mesh_terms, list):
        pass
    else:
        mesh_terms = []
    if mesh_terms:
        text_parts.append(f"MeSH词：{', '.join(mesh_terms)}")

    if article.get("year"):
        text_parts.append(f"年份：{article['year']}")
    if article.get("omics_type"):
        text_parts.append(f"组学类型：{article['omics_type']}")
    if article.get("pmid"):
        text_parts.append(f"PMID：{article['pmid']}")
        text_parts.append(f"链接：https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/")
    if article.get("doi"):
        doi = str(article["doi"]).removeprefix("https://doi.org/")
        text_parts.append(f"DOI：{doi}")

    if genes:
        text_parts.append("基因ID：")
        text_parts.extend(f"{mention} => {curie}" for mention, curie in genes.items())

    return "\n".join(text_parts)
