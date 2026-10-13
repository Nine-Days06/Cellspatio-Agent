"""pubtator：PubTator3 export 解析、分批、400 拆半、缓存往返（全部离线）。"""

import httpx

from src.data import pubtator

# 照实测 9704485 裁剪：标题/摘要行（管道分隔）+ 标注行（TAB 6 列）+ 关系行（4 列）
_FIXTURE = (
    "9704485|t|CD34+ subset and tumor cell quantitation.\n"
    "9704485|a|We analysed CD34 and CD38 expression in CD61 cells.\n"
    "9704485\t0\t4\tCD34\tGene\t947\n"
    "9704485\t17\t22\ttumor\tDisease\tMESH:D009369\n"
    "9704485\t594\t598\tCD38\tGene\t952\n"
    "9704485\t629\t633\tCD61\tGene\t3690\n"
    "9704485\t257\t261\tCD34\tGene\t947\n"          # 重复 GeneID → 去重
    "9704485\t429\t437\tpatients\tSpecies\t9606\n"
    "9704485\tAssociation\tMESH:D016393\t947\n"      # 关系行不得误取
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_parses_gene_lines_only():
    """只取 Type==Gene 的 6 列标注行，非基因实体与关系行忽略。"""
    def handler(request):
        return httpx.Response(200, text=_FIXTURE)

    result = pubtator.fetch_pmid_genes(["9704485"], client=_client(handler), min_interval=0)
    assert result == {"9704485": ["NCBIGene:947", "NCBIGene:952", "NCBIGene:3690"]}


def test_fetch_splits_into_batches_of_100():
    """按 batch_size 分批，请求数 = ceil(n/batch)。"""
    calls = []

    def handler(request):
        calls.append(request.url.params.get("pmids"))
        return httpx.Response(200, text="")

    pmids = [str(i) for i in range(250)]
    pubtator.fetch_pmid_genes(pmids, client=_client(handler), batch_size=100, min_interval=0)
    assert len(calls) == 3
    assert calls[0].count(",") == 99      # 第一批 100 个
    assert calls[2].count(",") == 49      # 末批 50 个


def test_fetch_400_splits_then_skips_singleton():
    """整批不存在 → 400：拆半重试；单条仍 400 → 跳过不抛异常。"""
    def handler(request):
        return httpx.Response(400, json={"detail": "Could not retrieve publications"})

    result = pubtator.fetch_pmid_genes(
        ["999999999", "888888888"], client=_client(handler), min_interval=0
    )
    assert result == {}


def test_fetch_recovers_valid_pmids_from_mixed_batch():
    """一批里只有部分命中（200）时只返回命中项。"""
    def handler(request):
        return httpx.Response(200, text=_FIXTURE)

    result = pubtator.fetch_pmid_genes(
        ["9704485", "888888888"], client=_client(handler), min_interval=0
    )
    assert list(result) == ["9704485"]


def test_cache_roundtrip(tmp_path):
    """写缓存 → 读回一致（JSON）。"""
    path = tmp_path / "gene_ids.json"
    pubtator.save_pmid_genes({"9704485": ["NCBIGene:947"]}, path)
    assert pubtator.load_pmid_genes(path) == {"9704485": ["NCBIGene:947"]}


def test_load_cache_missing_or_corrupt_returns_empty(tmp_path):
    """缓存不存在/损坏 → 返回空（导入侧据此不注入，不报错）。"""
    assert pubtator.load_pmid_genes(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert pubtator.load_pmid_genes(bad) == {}
