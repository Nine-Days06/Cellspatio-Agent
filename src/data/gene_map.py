"""基因 ID 统一：NCBI gene_info → 符号/Ensembl/同义词 ↔ NCBIGene 映射

统一规范 ID 采用 NCBI GeneID（CURIE 形式 NCBIGene:NNNN）：与 KEGG 的 hsa:NNNN
（同值）及 PubTator3 的 ConceptID 同构，作为各源入库文本的共享 token，
使 LightRAG 图实体跨源合并。

映射表：data/raw/genemap/Homo_sapiens.gene_info.gz（NCBI，公有领域，日更，
实测 5,194,256 B）。实测 16 列；dbXrefs 内含 Ensembl（实测 38,514 行形如
MIM:191170|HGNC:HGNC:11998|Ensembl:ENSG00000141510）；Synonyms 以 | 分隔；
空值以 - 表示；文件为人类专用（tax_id 恒 9606）。
"""
import gzip
from pathlib import Path

from src.data.download import ensure_file

GENE_INFO_URL = (
    "https://ftp.ncbi.nlm.nih.gov/gene/DATA/GENE_INFO/Mammalia/Homo_sapiens.gene_info.gz"
)
ROOT = Path(__file__).resolve().parents[2]
GENE_INFO_PATH = ROOT / "data" / "raw" / "genemap" / "Homo_sapiens.gene_info.gz"

_INDEX: "GeneIndex | None" = None


def gene_curie(gene_id: str) -> str:
    """NCBI GeneID → 规范 CURIE token（图实体锚点）"""
    return f"NCBIGene:{gene_id}"


class GeneIndex:
    """gene_info 构建的只读索引：官方符号 / Ensembl / 同义词 → GeneID

    查询优先级：官方符号 → Ensembl → 同义词（同义词歧义最大，末位回退）。
    可直接用字典构造，便于测试注入而无需联网。
    """

    def __init__(
        self,
        by_symbol: dict[str, str],
        by_ensembl: dict[str, str],
        by_synonym: dict[str, str],
    ) -> None:
        self._symbol = by_symbol
        self._ensembl = by_ensembl
        self._synonym = by_synonym

    def lookup(self, *, symbol: str = "", ensembl: str = "") -> str:
        """返回 GeneID；未命中返回空串"""
        if symbol:
            hit = self._symbol.get(symbol.upper())
            if hit:
                return hit
        if ensembl:
            hit = self._ensembl.get(ensembl.upper())
            if hit:
                return hit
        if symbol:
            return self._synonym.get(symbol.upper(), "")
        return ""


def load_gene_index(path: Path | None = None) -> GeneIndex:
    """解析 gene_info 构建索引；未传路径时经 ensure_file 下载并缓存"""
    path = Path(path) if path else ensure_file(GENE_INFO_URL, GENE_INFO_PATH)
    by_symbol: dict[str, str] = {}
    by_ensembl: dict[str, str] = {}
    by_synonym: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        col = {name: i for i, name in enumerate(header)}
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < len(header):
                continue
            gene_id = cols[col["GeneID"]].strip()
            if not gene_id.isdigit():
                continue
            for key in (cols[col["Symbol"]], cols[col["Symbol_from_nomenclature_authority"]]):
                key = key.strip()
                if key and key != "-":
                    by_symbol.setdefault(key.upper(), gene_id)
            for syn in cols[col["Synonyms"]].split("|"):
                syn = syn.strip()
                if syn and syn != "-":
                    by_synonym.setdefault(syn.upper(), gene_id)
            for xref in cols[col["dbXrefs"]].split("|"):
                if xref.startswith("Ensembl:"):
                    by_ensembl.setdefault(xref[len("Ensembl:"):].upper(), gene_id)
    return GeneIndex(by_symbol, by_ensembl, by_synonym)


def get_gene_index() -> GeneIndex:
    """进程内单例：首次调用解析 gene_info，之后复用（rebuild 每篇文档都要查）"""
    global _INDEX
    if _INDEX is None:
        _INDEX = load_gene_index()
    return _INDEX
