"""真实抽取金丝雀：小样本文献入库 → 读实体 → 判定抽取质量（fake ID / CJK / 书目噪声）。

用法（需真实 LLM/Embedding，由用户手动执行；禁止在单测/CI 中跑）：
    python canary_extract.py --n 15 --working-dir .canary_kb

exit 0 = ok（三类噪声皆空，可进入全量导入），1 = 有噪声。
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 噪声判定模式（纯正则，无第三方依赖）
FAKE_ID_RE = re.compile(r"^NCBIGene:[^0-9]")
# CJK 统一表意 + 扩展A + 兼容表意 + 假名 + 谚文
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
BIBLIO_PATTERNS = (re.compile(r"^\d{7,9}$"), re.compile(r"^10\.\d{3,9}/"))


def judge_entities(names: list[str]) -> dict:
    """按三类噪声判定抽取实体名；三类皆空则 ok=True（可进全量导入）"""
    fake_id = [n for n in names if FAKE_ID_RE.match(n)]
    cjk = [n for n in names if CJK_RE.search(n)]
    bibliographic = [n for n in names if any(p.match(n) for p in BIBLIO_PATTERNS)]
    return {
        "fake_id": fake_id,
        "cjk": cjk,
        "bibliographic": bibliographic,
        "ok": not fake_id and not cjk and not bibliographic,
    }


def _load_articles(n: int, mapping: dict) -> list[dict]:
    """读 data/import/*.csv 中前 n 篇有基因映射的文献（按 CSV 行序）"""
    articles: list[dict] = []
    for csv_path in sorted((ROOT / "data" / "import").glob("*.csv")):
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("pmid") or "").strip() in mapping:
                    articles.append(row)
                    if len(articles) >= n:
                        return articles
    return articles


def _read_entity_names(working_dir: str) -> list[str]:
    """读金丝雀库 vdb_entities.json 的实体名列表（文件缺失按空处理）"""
    vdb_path = Path(working_dir) / "vdb_entities.json"
    if not vdb_path.exists():
        return []
    payload = json.loads(vdb_path.read_text(encoding="utf-8"))
    return [
        str(r.get("entity_name") or r.get("__id__") or "")
        for r in (payload.get("data") or [])
    ]


def run_canary(n: int = 15, working_dir: str = ".canary_kb") -> dict:
    """取前 n 篇有基因映射的文献 → 注入基因映射文本 → 抽取入库 → 判定并打印报告"""
    from src.data.pubtator import load_pmid_genes
    from src.knowledge.article_text import convert_article_to_text
    from src.knowledge.lightrag_client import LightRAGClient

    mapping = load_pmid_genes()
    articles = _load_articles(n, mapping)
    texts = [
        convert_article_to_text(row, genes=mapping[(row.get("pmid") or "").strip()])
        for row in articles
    ]
    LightRAGClient(working_dir=working_dir).insert_documents(texts)
    names = _read_entity_names(working_dir)
    report = judge_entities(names)
    print(f"金丝雀：{len(texts)} 篇入库，抽取实体 {len(names)} 个")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实抽取金丝雀质量门（需 LLM/Embedding）")
    parser.add_argument("--n", type=int, default=15, help="抽样篇数")
    parser.add_argument("--working-dir", default=".canary_kb", help="金丝雀知识库目录")
    args = parser.parse_args(argv)
    report = run_canary(n=args.n, working_dir=args.working_dir)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
