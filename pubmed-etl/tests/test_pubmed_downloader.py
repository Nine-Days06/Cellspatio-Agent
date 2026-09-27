import unittest
from unittest.mock import Mock, patch

from config.settings import SEARCH_YEAR_MAX, SEARCH_YEAR_MIN
from downloader.pubmed_downloader import _collect_pmids, _split_range, fetch_pmid_list


class TestPubmedDownloaderDateRange(unittest.TestCase):
    @patch("downloader.pubmed_downloader.time.sleep")
    @patch("downloader.pubmed_downloader._safe_json")
    @patch("downloader.pubmed_downloader._get")
    def test_esearch_should_include_2020_to_now_date_range(self, mock_get, mock_safe_json, _mock_sleep):
        mock_safe_json.return_value = {
            "esearchresult": {
                "count": "2",
                "webenv": "test_webenv",
                "querykey": "1",
            }
        }

        esearch_resp = Mock()
        efetch_resp = Mock()
        efetch_resp.text = "1\n2\n"
        mock_get.side_effect = [esearch_resp, efetch_resp]

        pmids = fetch_pmid_list("potato")

        self.assertEqual(pmids, ["1", "2"])
        first_call_args, _ = mock_get.call_args_list[0]
        params = first_call_args[1]
        self.assertEqual(params["datetype"], "pdat")
        self.assertEqual(params["mindate"], str(SEARCH_YEAR_MIN))
        self.assertEqual(params["maxdate"], str(SEARCH_YEAR_MAX))


class TestSplitRange(unittest.TestCase):
    def test_halves_are_disjoint_and_adjacent(self):
        from datetime import datetime, timedelta
        left, right = _split_range("2020/01/01", "2020/12/31")
        # 互斥且相邻：左段结束的下一天就是右段开始
        nxt = (
            datetime.strptime(left[1], "%Y/%m/%d") + timedelta(days=1)
        ).strftime("%Y/%m/%d")
        self.assertEqual(right[0], nxt)
        self.assertEqual(left[0], "2020/01/01")
        self.assertEqual(right[1], "2020/12/31")

    def test_single_day_returns_none(self):
        self.assertIsNone(_split_range("2025/06/01", "2025/06/01"))


class TestCollectPmidsAdaptiveSplit(unittest.TestCase):
    """自适应切分：超 10k 的切片递归对半拆，叶子直接翻页取"""

    @patch("downloader.pubmed_downloader.fetch_pmid_list")
    @patch("downloader.pubmed_downloader._fetch_count")
    def test_overflow_range_is_split_and_concatenated(self, mock_count, mock_fetch):
        from datetime import datetime, timedelta
        # 顶层 10000+ 超限 → 两个叶子各 6000 达标
        mock_count.side_effect = [12000, 6000, 6000]
        mock_fetch.side_effect = [["1", "2"], ["3"]]

        pmids = _collect_pmids("q", "2025/01/01", "2025/12/31")

        self.assertEqual(pmids, ["1", "2", "3"])
        self.assertEqual(mock_fetch.call_count, 2)
        # 叶子日期范围：覆盖原区间、互斥、相邻
        l = mock_fetch.call_args_list[0].kwargs
        r = mock_fetch.call_args_list[1].kwargs
        self.assertEqual(l["mindate"], "2025/01/01")
        self.assertEqual(r["maxdate"], "2025/12/31")
        nxt = (
            datetime.strptime(l["maxdate"], "%Y/%m/%d") + timedelta(days=1)
        ).strftime("%Y/%m/%d")
        self.assertEqual(r["mindate"], nxt)

    @patch("downloader.pubmed_downloader.fetch_pmid_list")
    @patch("downloader.pubmed_downloader._fetch_count")
    def test_single_day_overflow_falls_back_to_fetch(self, mock_count, mock_fetch):
        # 单日仍超限（理论不可达）→ 降级直接翻页，不无限递归
        mock_count.return_value = 10001
        mock_fetch.return_value = ["9"]

        pmids = _collect_pmids("q", "2025/06/01", "2025/06/01")

        self.assertEqual(pmids, ["9"])
        mock_fetch.assert_called_once_with("q", mindate="2025/06/01", maxdate="2025/06/01")

    @patch("downloader.pubmed_downloader.fetch_pmid_list")
    @patch("downloader.pubmed_downloader._fetch_count")
    def test_within_limit_fetches_directly(self, mock_count, mock_fetch):
        mock_count.return_value = 9500
        mock_fetch.return_value = ["1"]

        pmids = _collect_pmids("q", "2000/01/01", "2004/12/31")

        self.assertEqual(pmids, ["1"])
        mock_fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
