import unittest
from unittest.mock import Mock, patch

from config.settings import SEARCH_YEAR_MIN, SEARCH_YEAR_MAX
from downloader.pubmed_downloader import fetch_pmid_list


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


if __name__ == "__main__":
    unittest.main()
