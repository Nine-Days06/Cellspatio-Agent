"""xml_parser 回归测试：PubMed XML 中 doi/pmc 只应取自 ArticleIdList，
不得被 ReferenceList（参考文献）里的 ArticleId 覆盖。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser.xml_parser import parse_article
from lxml import etree

XML_WITH_REFS = """<PubmedArticle>
  <MedlineCitation>
    <PMID>42401621</PMID>
    <Article>
      <ArticleTitle>Multi-omics profiling of high-carotenoid hybrid potato lines</ArticleTitle>
      <Abstract>
        <AbstractText>Potato multi-omics study.</AbstractText>
      </Abstract>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">42401621</ArticleId>
      <ArticleId IdType="doi">10.1038/s41538-026-00842-3</ArticleId>
      <ArticleId IdType="pmc">PMC13338389</ArticleId>
    </ArticleIdList>
    <ReferenceList>
      <Reference>
        <ArticleIdList>
          <ArticleId IdType="pubmed">36090468</ArticleId>
          <ArticleId IdType="doi">10.1016/j.tifs.2022.06.011</ArticleId>
          <ArticleId IdType="pmc">PMC9449372</ArticleId>
        </ArticleIdList>
      </Reference>
    </ReferenceList>
  </PubmedData>
</PubmedArticle>"""


class TestParseArticleIdType(unittest.TestCase):
    def test_should_not_take_article_id_from_reference_list(self):
        root = etree.fromstring(XML_WITH_REFS)
        rec = parse_article(root, "batch_test.xml")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["pmid"], "42401621")
        self.assertEqual(rec["doi"], "10.1038/s41538-026-00842-3")
        self.assertEqual(rec["pmc_id"], "PMC13338389")

    def test_should_leave_fields_empty_when_no_article_id_list(self):
        xml_no_refs = "<PubmedArticle><MedlineCitation><PMID>999</PMID><Article><ArticleTitle>T</ArticleTitle></Article></MedlineCitation></PubmedArticle>"
        root = etree.fromstring(xml_no_refs)
        rec = parse_article(root, "batch_test.xml")
        self.assertEqual(rec["doi"], "")
        self.assertEqual(rec["pmc_id"], "")


if __name__ == "__main__":
    unittest.main()