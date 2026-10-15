"""真实抽取金丝雀：小样本文献入库 → 读实体 → 判定抽取质量（fake ID / CJK / 书目噪声）。

用法（需真实 LLM/Embedding，由用户手动执行；禁止在单测/CI 中跑）：
    python canary_extract.py --n 15 --working-dir .canary_kb

exit 0 = ok（四类噪声皆空，可进入全量导入），1 = 有噪声。
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 噪声判定模式（纯正则，无第三方依赖）
FAKE_ID_RE = re.compile(r"^NCBIGene:[^0-9]")
# CJK 统一表意 + 扩展A + 兼容表意 + 假名 + 谚文
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
BIBLIO_PATTERNS = (re.compile(r"^\d{7,9}$"), re.compile(r"^10\.\d{3,9}/"))


def judge_entities(names: list[str], banned: list[str] | None = None) -> dict[str, Any]:
    """按四类噪声判定抽取实体名（fake_id / cjk / 书目 / 标题句）；四类皆空则 ok=True（可进全量导入）

    Args:
        names: 抽取出的实体名列表。
        banned: 禁词表（文献标题等书目字符串），精确命中即算书目噪声。
    """
    banned_lower = {b.strip().lower() for b in (banned or [])}
    fake_id = [n for n in names if FAKE_ID_RE.match(n)]
    cjk = [n for n in names if CJK_RE.search(n)]
    bibliographic = [
        n
        for n in names
        if any(p.match(n) for p in BIBLIO_PATTERNS) or n.strip().lower() in banned_lower
    ]
    title_like = [n for n in names if len(n.split()) >= 12]
    return {
        "fake_id": fake_id,
        "cjk": cjk,
        "bibliographic": bibliographic,
        "title_like": title_like,
        "ok": not fake_id and not cjk and not bibliographic and not title_like,
    }


def _load_articles(n: int) -> list[dict]:
    """读 data/import/*.csv 前 n 篇文献（按 CSV 行序，不看有无基因映射）"""
    articles: list[dict] = []
    for csv_path in sorted((ROOT / "data" / "import").glob("*.csv")):
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
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
    return [str(r.get("entity_name") or r.get("__id__") or "") for r in (payload.get("data") or [])]


def run_canary(n: int = 15, working_dir: str = ".canary_kb") -> dict[str, Any]:
    """取 data/import/*.csv 前 n 篇文献 → 注入基因映射文本 → 抽取入库 → 收集禁词并判定"""
    from src.data.pubtator import load_pmid_genes
    from src.knowledge.article_text import convert_article_to_text
    from src.knowledge.lightrag_client import LightRAGClient

    mapping = load_pmid_genes()
    articles = _load_articles(n)
    texts = [
        convert_article_to_text(row, genes=mapping.get((row.get("pmid") or "").strip()))
        for row in articles
    ]
    # 禁词只收标题：`作者：/期刊：` 已从抽取文本移除，不会再成为实体；且期刊名常与
    # 生物学术语同名（如期刊 "Cytokine" vs 术语 "Cytokine"），精确匹配会误报。
    banned = [row["title"] for row in articles if row.get("title")]
    LightRAGClient(working_dir=working_dir).insert_documents(texts)
    names = _read_entity_names(working_dir)
    report = judge_entities(names, banned)
    print(f"金丝雀：{len(texts)} 篇入库，抽取实体 {len(names)} 个")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数并运行金丝雀，返回退出码（0=ok，1=有噪声）"""
    parser = argparse.ArgumentParser(description="真实抽取金丝雀质量门（需 LLM/Embedding）")
    parser.add_argument("--n", type=int, default=15, help="抽样篇数")
    parser.add_argument("--working-dir", default=".canary_kb", help="金丝雀知识库目录")
    args = parser.parse_args(argv)
    report = run_canary(n=args.n, working_dir=args.working_dir)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
