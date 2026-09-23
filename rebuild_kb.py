"""删库重进：清空 knowledge_base 后按原 20 条 ID 重新入库（带来源 URL）"""
from __future__ import annotations

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

KEGG_IDS = [
    "map04950", "map04214", "map04215", "map04115",
    "map04930", "map04210", "map04940", "map04110",
]
UNIPROT_IDS = [
    "Q9NQ88", "P58012", "Q14644", "Q13572", "Q13813",
    "Q9Y2B4", "P04637", "Q12888", "Q7LG56", "P01308",
]
GEO_IDS = ["GSE123456", "GSE48351"]


def clear_kb() -> None:
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
    clear_kb()
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
