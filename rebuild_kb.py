"""删库重进：清空 knowledge_base 后按原 20 条 ID 重新入库（带来源 URL）

⚠️ 安全约束：clear_kb() 会清空整个 knowledge_base/ 目录，其中也包含
pubmed-etl 文献导入的 chunk、实体与关系。若文献已入库，跑本脚本会**静默删除**它们。
因此清库前会检查 kv_store_full_docs.json：只要发现非「公共数据库 20 条」来源的
文档（例如带 PMID / pubmed 链接的文献），就拒绝执行并提示。确需清空时显式传 --force。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")

KB_DIR = ROOT / "knowledge_base"
KEEP = {"EMBEDDING_MODEL.json"}

# 20 条基线 ID（来源 ca86047，2026-04 逐条实测核实）
# KEGG 换 hsa 物种通路：map* 参考通路无 GENE/物种段（0 基因），hsa* 含 Entrez ID；
# map04214（果蝇凋亡）无人类对应（hsa04214 404），按主题以 hsa04151 PI3K-Akt 替换。
KEGG_IDS = [
    "hsa04950", "hsa04151", "hsa04215", "hsa04115",   # MODY / PI3K-Akt / 凋亡多物种 / p53
    "hsa04930", "hsa04210", "hsa04940", "hsa04110",   # II型糖尿病 / 凋亡 / I型糖尿病 / 细胞周期
]
# UniProt 10 条：全部 Homo sapiens，GeneID 位于 uniProtKBCrossReferences（genes[].geneId 实测为空）
UNIPROT_IDS = [
    "Q9NQ88", "P58012", "Q14644", "Q13572", "Q13813",
    "Q9Y2B4", "P04637", "Q12888", "Q7LG56", "P01308",
]
# GEO 2 条：均为真实公开数据集（GSE123456 4 样本 / GSE48351 10 样本，PMID 见 esummary）
GEO_IDS = ["GSE123456", "GSE48351"]

# 本脚本负责重建的公共数据库来源标记（由各 ingest_text 写入）
CURATED_HEADERS = ("# KEGG 通路:", "# UniProt 蛋白:", "# GEO 数据集:")
DOCS_FILE = "kv_store_full_docs.json"


def find_foreign_docs() -> list[str]:
    """返回不属于公共数据库 20 条来源的文档 ID 列表；无库返回空。

    判据：文档正文不含任一 CURATED_HEADERS，即视为外部导入（目前只有文献）。
    文件存在但读取/解析失败时**拒绝清库**（fail-closed）——无法确认内容就
    不能删，避免误删文献；此时先备份或修复 knowledge_base/ 后再执行。
    """
    docs_path = KB_DIR / DOCS_FILE
    if not docs_path.exists():
        return []
    try:
        raw = json.loads(docs_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"[拒绝] 读取 {DOCS_FILE} 失败，无法校验文档来源：{exc}", file=sys.stderr)
        print(
            "       无法确认库中是否含文献等非公共数据库内容，拒绝清库（fail-closed）。",
            file=sys.stderr,
        )
        print(
            "       请先备份 knowledge_base/ 并修复该文件后重试。",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    foreign: list[str] = []
    for doc_id, doc in raw.items():
        content = doc.get("content", "") if isinstance(doc, dict) else str(doc)
        if not any(h in content for h in CURATED_HEADERS):
            foreign.append(str(doc_id))
    return foreign


def clear_kb(*, force: bool = False) -> None:
    """清空知识库目录（保留 KEEP）。

    Raises:
        SystemExit: 检测到文献等非公共数据库来源的文档且未传 force。
    """
    foreign = find_foreign_docs()
    if foreign and not force:
        print(
            f"[拒绝] {KB_DIR.name}/ 中检测到 {len(foreign)} 个非公共数据库来源的文档"
            "（例如 pubmed-etl 导入的文献）。",
            file=sys.stderr,
        )
        print(
            "       clear_kb() 会删除整个目录，文献 chunk/实体/关系会一并丢失。",
            file=sys.stderr,
        )
        print(
            "       如确认要连同文献一起清空，请显式传 --force；"
            "否则先备份 knowledge_base/ 再手工删除。",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if foreign:
        print(f"[force] 忽略 {len(foreign)} 个非公共数据库来源的文档，继续清库")

    KB_DIR.mkdir(parents=True, exist_ok=True)
    for p in KB_DIR.iterdir():
        if p.name in KEEP:
            continue
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            for c in p.iterdir():
                if c.is_file():
                    c.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="清空并重建公共数据库知识库（20 条）")
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使库中存在文献等非公共数据库来源的文档也强制清空",
    )
    args = parser.parse_args()

    clear_kb(force=args.force)
    print("KB cleared (kept EMBEDDING_MODEL.json)")

    from src.knowledge.knowledge_builder import KnowledgeBuilder
    from src.knowledge.lightrag_client import LightRAGClient

    client = LightRAGClient(working_dir=str(KB_DIR), config={})
    builder = KnowledgeBuilder(client)

    results = []
    results.append(("kegg", builder.build_from_kegg(KEGG_IDS)))
    results.append(("uniprot", builder.build_from_uniprot(UNIPROT_IDS)))
    results.append(("geo", builder.build_from_geo_metadata(GEO_IDS)))

    total = 0
    for name, r in results:
        print(name, r)
        total += int(r.get("inserted", 0) or 0)
    print("TOTAL_INSERTED", total)
    return 0 if total == 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
