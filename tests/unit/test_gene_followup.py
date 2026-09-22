"""基因追问文案（纯函数）"""
from src.ui.components import gene_followup_prompt


def test_gene_followup_prompt_contains_gene():
    assert "BRCA1" in gene_followup_prompt("BRCA1")
    assert gene_followup_prompt("").strip() == ""


def test_gene_followup_prompt_is_knowledge_style():
    text = gene_followup_prompt("TP53")
    assert "是什么" in text or "查询" in text or "作用" in text
