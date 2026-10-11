import httpx

from src.data.fetchers.kegg_fetcher import KEGGFetcher


def _fetcher_with(handler):
    return KEGGFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_search_parses_find_response():
    def handler(request):
        assert "/find/pathway/glycolysis" in str(request.url)
        return httpx.Response(200, text=(
            "path:hsa00010\tGlycolysis / Gluconeogenesis - Homo sapiens (human)\n"
            "path:ec00010\tGlycolysis / Gluconeogenesis - Escherichia coli K-12\n"
        ))

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("glycolysis")
    assert len(metas) == 2
    assert metas[0].asset_id == "hsa00010"
    assert metas[0].asset_type == "knowledge"
    assert "Glycolysis" in metas[0].title


def test_search_respects_max_results():
    def handler(request):
        return httpx.Response(200, text="path:one001\tA\npath:one002\tB\npath:one003\tC\n")

    fetcher = _fetcher_with(handler)
    metas = fetcher.search("query", max_results=2)
    assert len(metas) == 2


def test_confirm_parses_name_and_description():
    def handler(request):
        assert "/get/hsa00010" in str(request.url)
        return httpx.Response(200, text=(
            "ENTRY       hsa00010                      Pathway\n"
            "NAME        Glycolysis / Gluconeogenesis\n"
            "DESCRIPTION  Glycolysis is the metabolic pathway\n"
            "xxx\n"
            "//\n"
        ))

    fetcher = _fetcher_with(handler)
    info = fetcher.confirm("hsa00010")
    assert info.title == "Glycolysis / Gluconeogenesis"
    assert "metabolic pathway" in info.description


def test_ingest_text_wraps_raw_text():
    def handler(request):
        return httpx.Response(200, text="ENTRY       hsa00010\r\n//\r\n")

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_text("hsa00010")
    assert text.startswith("# KEGG 通路: hsa00010")
    assert "来源：https://www.kegg.jp/pathway/hsa00010" in text
    assert "ENTRY" in text


def test_download_raises_not_implemented():
    fetcher = KEGGFetcher()
    try:
        fetcher.download("hsa00010")
    except NotImplementedError:
        assert True
    else:
        raise AssertionError("download 应抛 NotImplementedError")


KGML_FIXTURE = """<?xml version="1.0"?>
<!DOCTYPE pathway SYSTEM "http://www.kegg.jp/keggml.dtd">
<pathway name="path:hsa04110" org="hsa" number="04110" title="Cell cycle">
  <entry id="4" name="hsa:1029" type="gene" link="https://www.kegg.jp/dbget-bin/www_bget?hsa:1029">
    <graphics name="CDKN2A" x="532" y="218" width="46" height="17" type="rectangle"/>
  </entry>
  <entry id="54" name="hsa:4171 hsa:4172" type="gene" link="https://www.kegg.jp/dbget-bin/www_bget?hsa:4171">
    <graphics name="MCM2, MCM3" x="300" y="300" width="46" height="17" type="rectangle"/>
  </entry>
  <entry id="290" name="map04110" type="map">
    <graphics name="Cell cycle" x="100" y="100" width="46" height="17" type="rectangle"/>
  </entry>
  <entry id="300" name="cpd:C00002" type="compound">
    <graphics name="ATP" x="50" y="50" width="46" height="17" type="circle"/>
  </entry>
  <relation entry1="290" entry2="54" type="GErel">
    <subtype name="expression" value="--&gt;"/>
  </relation>
  <relation entry1="54" entry2="4" type="PPrel">
    <subtype name="phosphorylation" value="+"/>
  </relation>
  <reaction id="100" name="rn:R00001" type="irreversible">
    <substrate id="300"/>
    <product id="4"/>
  </reaction>
</pathway>
"""


def test_ingest_kgml_text_renders_structure():
    def handler(request):
        assert "/get/hsa04110/kgml" in str(request.url)
        return httpx.Response(200, text=KGML_FIXTURE)

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_kgml_text("hsa04110")
    assert text.startswith("# KEGG 通路结构: hsa04110")
    assert "名称：Cell cycle" in text
    assert "基因（3）: CDKN2A, MCM2, MCM3" in text
    assert "化合物（1）: ATP" in text
    # relation/reaction 的 entry id 必须映射为显示名
    assert "关系（2）:" in text
    assert "- Cell cycle -| MCM2, MCM3 (expression)" in text
    assert "- MCM2, MCM3 → CDKN2A (phosphorylation)" in text
    assert "反应（1）:" in text
    assert "- ATP → CDKN2A" in text


KGML_NO_REL_FIXTURE = """<pathway name="path:hsa00010" org="hsa" number="00010" title="Glycolysis">
  <entry id="1" name="hsa:5213" type="gene">
    <graphics name="PFKL" x="1" y="1" width="46" height="17" type="rectangle"/>
  </entry>
</pathway>
"""


def test_ingest_kgml_text_without_relations_or_reactions():
    """无 relation/reaction 的通路（信号图常见 0 reaction）渲染"无"占位行"""
    def handler(request):
        return httpx.Response(200, text=KGML_NO_REL_FIXTURE)

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_kgml_text("hsa00010")
    assert "基因（1）: PFKL" in text
    assert "关系（0）: 无" in text
    assert "反应（0）: 无" in text


def test_kgml_falls_back_to_name_when_graphics_missing():
    """entry 无 graphics 时，显示名回退用 name 属性"""
    def handler(request):
        return httpx.Response(200, text=(
            '<pathway title="T">'
            '<entry id="1" name="hsa:7157" type="gene"/>'
            '<entry id="2" name="hsa:1956" type="gene"/>'
            '<relation entry1="1" entry2="2" type="GErel"/>'
            '</pathway>'
        ))

    fetcher = _fetcher_with(handler)
    text = fetcher.ingest_kgml_text("hsaXXXXX")
    assert "基因（2）: hsa:7157, hsa:1956" in text
    assert "- hsa:7157 -| hsa:1956" in text