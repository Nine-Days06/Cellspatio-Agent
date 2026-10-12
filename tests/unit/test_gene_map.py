"""gene_map：gene_info 索引构建与查询优先级（fixture 照真实 16 列形态）。"""

import gzip

from src.data.gene_map import GeneIndex, gene_curie, load_gene_index

# 照实测真实行形态裁剪（A1BG=1 / TP53=7157 / MCM2=4171）
_HEADER = (
    "#tax_id\tGeneID\tSymbol\tLocusTag\tSynonyms\tdbXrefs\tchromosome\tmap_location\t"
    "description\ttype_of_gene\tSymbol_from_nomenclature_authority\t"
    "Full_name_from_nomenclature_authority\tNomenclature_status\tOther_designations\t"
    "Modification_date\tFeature_type"
)
_A1BG = (
    "9606\t1\tA1BG\t-\tA1B|ABG|GAB\tMIM:138670|HGNC:HGNC:5|Ensembl:ENSG00000121410|"
    "AllianceGenome:HGNC:5\t19\t19q13.43\talpha-1-B glycoprotein\tprotein-coding\tA1BG\t"
    "alpha-1-B glycoprotein\tO\tA1BG\t20260918\tprotein-coding"
)
_TP53 = (
    "9606\t7157\tTP53\t-\tBCC7|LFS1|P53|TRP53\tMIM:191170|HGNC:HGNC:11998|"
    "Ensembl:ENSG00000141510|AllianceGenome:HGNC:11998\t17\t17p13.1\ttumor protein p53\t"
    "protein-coding\tTP53\ttumor protein p53\tO\tP53\t20260918\tprotein-coding"
)
_MCM2 = (
    "9606\t4171\tMCM2\t-\tBM28|CCNL1|CDCL1\tMIM:609357|HGNC:HGNC:6944|"
    "Ensembl:ENSG00000073111\t3\t3q21.3\tminichromosome maintenance complex component 2\t"
    "protein-coding\tMCM2\tminichromosome maintenance complex component 2\tO\t-\t20260918\t"
    "protein-coding"
)


def _write_gene_info(tmp_path):
    """把迷你 gene_info（真实 16 列形态）写成 gzip 文件，返回路径。"""
    path = tmp_path / "Homo_sapiens.gene_info.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(f"{_HEADER}\n{_A1BG}\n{_TP53}\n{_MCM2}\n")
    return path


def test_load_index_maps_symbol_ensembl_and_synonym(tmp_path):
    """官方符号 / Ensembl（取自 dbXrefs）/ 同义词三条通路都能命中。"""
    index = load_gene_index(_write_gene_info(tmp_path))
    assert index.lookup(symbol="TP53") == "7157"
    assert index.lookup(ensembl="ENSG00000141510") == "7157"
    assert index.lookup(symbol="P53") == "7157"      # 同义词回退
    assert index.lookup(symbol="MCM2") == "4171"


def test_lookup_case_insensitive_and_symbol_priority(tmp_path):
    """大小写不敏感；符号优先于同义词。"""
    index = load_gene_index(_write_gene_info(tmp_path))
    assert index.lookup(symbol="tp53") == "7157"
    assert index.lookup(symbol="ABG") == "1"         # 同义词
    assert index.lookup(symbol="A1BG") == "1"        # 官方符号


def test_lookup_unmatched_returns_empty(tmp_path):
    """未命中/空查询返回空串（调用方据此省略 ID 行）。"""
    index = load_gene_index(_write_gene_info(tmp_path))
    assert index.lookup(symbol="NOT_A_REAL_GENE_XYZ") == ""
    assert index.lookup(symbol="", ensembl="") == ""
    assert index.lookup(ensembl="ENSG00000000000") == ""


def test_dash_values_not_indexed(tmp_path):
    """gene_info 空值以 '-' 表示，不得进索引。"""
    path = tmp_path / "g.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(
            f"{_HEADER}\n"
            "9606\t99999\tDUMMY\t-\t-\t-\t1\t1p1\tdummy\tprotein-coding\t-\t-\t-\t-\t20260918\t-\n"
        )
    index = load_gene_index(path)
    assert index.lookup(symbol="DUMMY") == "99999"
    assert index.lookup(symbol="-") == ""


def test_gene_curie_token():
    """规范 token 形式：NCBIGene:<GeneID>。"""
    assert gene_curie("7157") == "NCBIGene:7157"


def test_gene_index_constructible_from_dicts():
    """公开构造：测试可注入小型索引，避免单测联网下载 gene_info。"""
    index = GeneIndex({"CD4": "920"}, {"ENSG00000010610": "920"}, {})
    assert index.lookup(symbol="CD4") == "920"
    assert index.lookup(ensembl="ENSG00000010610") == "920"
    assert index.lookup(symbol="CD4X") == ""
