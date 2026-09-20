import unittest
from unittest.mock import patch, Mock
import io
import tarfile
import tempfile
import csv
from pathlib import Path

from downloader.pdf_downloader import (
    normalize_pmc_asset_url,
    normalize_pmc_id,
    fetch_oa_links,
    load_cached_oa_links,
    download_pdf_file,
    download_pdf_from_tgz,
    download_oa_pdf,
    export_oa_links_csv,
)
from utils.db import CREATE_ARTICLES_SQL, CREATE_LLM_VALIDATION_SQL


class TestPdfDownloaderUrlNormalize(unittest.TestCase):
    def test_convert_ftp_to_https_and_deprecated_prefix(self):
        old_url = "ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/bb/cc/jmdh-8-091.PMC4334330.pdf"
        expected = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/oa_pdf/bb/cc/jmdh-8-091.PMC4334330.pdf"
        self.assertEqual(normalize_pmc_asset_url(old_url), expected)

    def test_keep_already_migrated_url(self):
        url = "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/oa_package/bb/cc/PMC4334330.tar.gz"
        self.assertEqual(normalize_pmc_asset_url(url), url)


class TestPmcIdNormalize(unittest.TestCase):
    def test_numeric_id_should_add_prefix(self):
        self.assertEqual(normalize_pmc_id("4334330"), "PMC4334330")

    def test_prefixed_id_should_uppercase(self):
        self.assertEqual(normalize_pmc_id("pmc4334330"), "PMC4334330")

    def test_invalid_id_should_return_none(self):
        self.assertIsNone(normalize_pmc_id("PMCABC"))
        self.assertIsNone(normalize_pmc_id(""))


class TestFetchOaLinks(unittest.TestCase):
    @patch("downloader.pdf_downloader.time.sleep")
    @patch("downloader.pdf_downloader.requests.get")
    def test_should_fetch_batch_and_parse_pdf_tgz(self, mock_get, _mock_sleep):
        def build_resp(xml_text: str):
            resp = Mock()
            resp.status_code = 200
            resp.content = xml_text.encode("utf-8")
            resp.raise_for_status = Mock()
            return resp

        def fake_get(_url, params=None, timeout=30, **kwargs):
            self.assertEqual(timeout, 30)
            if params.get("id") == "PMC4334330":
                return build_resp(
                    "<OA><records><record id='PMC4334330'>"
                    "<link format='pdf' href='ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/a/b/test.PMC4334330.pdf' />"
                    "<link format='tgz' href='ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/a/b/PMC4334330.tar.gz' />"
                    "</record></records></OA>"
                )
            return build_resp(
                "<OA><error code='idDoesNotExist'>PMCXXXX</error></OA>"
            )

        mock_get.side_effect = fake_get

        result, failed = fetch_oa_links(["PMC4334330", "PMCXXXX"])

        self.assertEqual(
            result["PMC4334330"]["pdf"],
            "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/oa_pdf/a/b/test.PMC4334330.pdf",
        )
        self.assertEqual(
            result["PMC4334330"]["tgz"],
            "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/oa_package/a/b/PMC4334330.tar.gz",
        )
        self.assertNotIn("PMCXXXX", result)
        self.assertEqual(failed, [])
        self.assertEqual(mock_get.call_count, 2)

    @patch("downloader.pdf_downloader.time.sleep")
    @patch("downloader.pdf_downloader.requests.get")
    def test_should_retry_single_query_for_unresolved_ids(self, mock_get, _mock_sleep):
        def build_resp(xml_text: str):
            resp = Mock()
            resp.status_code = 200
            resp.content = xml_text.encode("utf-8")
            resp.raise_for_status = Mock()
            return resp

        def fake_get(_url, params=None, timeout=30, **kwargs):
            if params == {"id": "PMC1"}:
                return build_resp(
                    "<OA><records><record id='PMC1'>"
                    "<link format='pdf' href='ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/a/b/1.pdf' />"
                    "</record></records></OA>"
                )

            if params == {"id": "PMC2"}:
                return build_resp(
                    "<OA><records><record id='PMC2'>"
                    "<link format='tgz' href='ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/a/b/2.tar.gz' />"
                    "</record></records></OA>"
                )

            return build_resp("<OA></OA>")

        mock_get.side_effect = fake_get

        result, _failed = fetch_oa_links(["PMC1", "PMC2"])

        self.assertIn("PMC1", result)
        self.assertIn("PMC2", result)
        self.assertEqual(mock_get.call_count, 2)

    @patch("downloader.pdf_downloader.requests.get")
    def test_should_reuse_cached_links_without_remote_fetch(self, mock_get):
        result, _failed = fetch_oa_links(
            ["PMC1"],
            cached_links={"PMC1": {"pdf": "https://example.org/1.pdf"}},
        )

        self.assertEqual(result["PMC1"]["pdf"], "https://example.org/1.pdf")
        mock_get.assert_not_called()


class TestLoadCachedOaLinks(unittest.TestCase):
    def test_should_load_latest_cached_links_by_pmc_id(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            old_file = out_dir / "oa_download_links_20260101_010101.csv"
            new_file = out_dir / "oa_download_links_20260102_010101.csv"

            old_file.write_text(
                "pmid,pmc_id,label,pdf_url,tgz_url\n"
                "111,PMC1,高相关,https://old.example/1.pdf,\n",
                encoding="utf-8",
            )
            new_file.write_text(
                "pmid,pmc_id,label,pdf_url,tgz_url\n"
                "111,PMC1,高相关,https://new.example/1.pdf,\n"
                "222,PMC2,中相关,,https://new.example/2.tgz\n",
                encoding="utf-8",
            )

            cached = load_cached_oa_links(["PMC1", "PMC2", "PMC3"], out_dir=out_dir)

        self.assertEqual(cached["PMC1"]["pdf"], "https://new.example/1.pdf")
        self.assertEqual(cached["PMC2"]["tgz"], "https://new.example/2.tgz")
        self.assertNotIn("PMC3", cached)


class TestDownloadPdfFromTgz(unittest.TestCase):
    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_extract_pdf_file_from_tgz(self, mock_run):
        pdf_payload = b"%PDF-1.4\n" + (b"A" * 3000)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="paper.pdf")
            info.size = len(pdf_payload)
            tar.addfile(info, io.BytesIO(pdf_payload))

        tgz_bytes = buf.getvalue()

        def fake_run(cmd, capture_output=True, text=True, timeout=300, check=False):
            out_dir = Path(cmd[cmd.index("-d") + 1])
            out_name = cmd[cmd.index("-o") + 1]
            out_path = out_dir / out_name
            out_path.write_bytes(tgz_bytes)
            return Mock(returncode=0, stderr="", stdout="")

        mock_run.side_effect = fake_run

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            ok = download_pdf_from_tgz("https://example.org/a.tgz", dest)

            self.assertTrue(ok)
            self.assertTrue(dest.exists())
            self.assertGreater(dest.stat().st_size, 1024)


class TestDownloadPdfFromTgzTxtFallback(unittest.TestCase):
    """tgz 包内无 PDF 时回退提取 nxml 转 txt"""

    def _make_tgz(self, members: list[tuple[str, bytes]]) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def _fake_run(self, tgz_bytes):
        def fake_run(cmd, capture_output=True, text=True, timeout=300, check=False):
            out_dir = Path(cmd[cmd.index("-d") + 1])
            out_name = cmd[cmd.index("-o") + 1]
            out_path = out_dir / out_name
            out_path.write_bytes(tgz_bytes)
            return Mock(returncode=0, stderr="", stdout="")
        return fake_run

    NXML = b"""<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
  <front><article-meta>
    <title-group><article-title>Potato CDF1 and drought</article-title></title-group>
  </article-meta></front>
  <body>
    <sec><title>Introduction</title>
      <p>Potato is an important crop.</p>
    </sec>
    <sec><title>Results</title>
      <p>We found interesting results.</p>
      <table-wrap><table><tr><td>a</td><td>b</td></tr></table></table-wrap>
    </sec>
  </body>
</article>"""

    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_extract_txt_when_no_pdf_in_tgz(self, mock_run):
        tgz_bytes = self._make_tgz([
            ("PMC1/main.nxml", self.NXML),
            ("PMC1/fig1.jpg", b"jpegdata"),
        ])
        mock_run.side_effect = self._fake_run(tgz_bytes)

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            ok = download_pdf_from_tgz("https://example.org/a.tgz", dest)

            txt_path = Path(td) / "out.txt"
            self.assertTrue(ok)
            self.assertFalse(dest.exists())
            self.assertTrue(txt_path.exists())
            content = txt_path.read_text(encoding="utf-8")
            self.assertIn("Potato is an important crop.", content)
            self.assertIn("Introduction", content)

    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_still_extract_pdf_when_pdf_present(self, mock_run):
        pdf_payload = b"%PDF-1.4\n" + (b"A" * 3000)
        tgz_bytes = self._make_tgz([
            ("PMC1/main.pdf", pdf_payload),
            ("PMC1/main.nxml", self.NXML),
        ])
        mock_run.side_effect = self._fake_run(tgz_bytes)

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            ok = download_pdf_from_tgz("https://example.org/a.tgz", dest)

            self.assertTrue(ok)
            self.assertTrue(dest.exists())
            self.assertFalse((Path(td) / "out.txt").exists())

    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_fail_when_no_pdf_and_no_nxml(self, mock_run):
        tgz_bytes = self._make_tgz([("fig1.jpg", b"jpegdata")])
        mock_run.side_effect = self._fake_run(tgz_bytes)

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            ok = download_pdf_from_tgz("https://example.org/a.tgz", dest)

            self.assertFalse(ok)
            self.assertFalse((Path(td) / "out.txt").exists())


class TestDownloadPdfFromTgzPicksArticlePdf(unittest.TestCase):
    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_pick_article_pdf_when_supplementary_pdf_comes_first(self, mock_run):
        article_pdf_payload = b"%PDF-1.4\n" + (b"ARTICLE-CONTENT-ABCD" * 300)
        supp_pdf_payload = b"%PDF-1.4\n" + (b"SUPPLEMENT-FIGURES-TABLES-YZ" * 300)
        buf = io.BytesIO()

        def add_member(tar, name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            add_member(tar, "PMC4334330/jmdh-8-091-s001.pdf", supp_pdf_payload)
            add_member(tar, "PMC4334330/jmdh-8-091.PMC4334330.pdf", article_pdf_payload)
            add_member(tar, "PMC4334330/jmdh-8-091.PMC4334330.nxml", b"<article/>")

        tgz_bytes = buf.getvalue()

        def fake_run(cmd, capture_output=True, text=True, timeout=300, check=False):
            out_dir = Path(cmd[cmd.index("-d") + 1])
            out_name = cmd[cmd.index("-o") + 1]
            out_path = out_dir / out_name
            out_path.write_bytes(tgz_bytes)
            return Mock(returncode=0, stderr="", stdout="")

        mock_run.side_effect = fake_run

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.pdf"
            ok = download_pdf_from_tgz("https://example.org/a.tgz", dest)

            self.assertTrue(ok)
            content = dest.read_bytes()
            self.assertIn(b"ARTICLE-CONTENT", content)
            self.assertNotIn(b"F002;SUPPLEMENT", content)


class TestDownloadOaPdf(unittest.TestCase):
    @patch("downloader.pdf_downloader.download_pdf_from_tgz")
    @patch("downloader.pdf_downloader.download_pdf_file")
    def test_should_download_pdf_direct_when_pdf_link_present(self, mock_pdf, mock_pdf_from_tgz):
        mock_pdf.return_value = True
        mock_pdf_from_tgz.return_value = True

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ok = download_oa_pdf(
                links={"pdf": "https://example.org/a.pdf", "tgz": "https://example.org/a.tgz"},
                pdf_path=base / "a.pdf",
            )

        self.assertTrue(ok)
        mock_pdf.assert_called_once()
        mock_pdf_from_tgz.assert_not_called()

    @patch("downloader.pdf_downloader.download_pdf_from_tgz")
    @patch("downloader.pdf_downloader.download_pdf_file")
    def test_should_extract_pdf_from_tgz_when_pdf_link_missing(self, mock_pdf, mock_pdf_from_tgz):
        mock_pdf.return_value = False
        mock_pdf_from_tgz.return_value = True

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ok = download_oa_pdf(
                links={"tgz": "https://example.org/a.tgz"},
                pdf_path=base / "a.pdf",
            )

        self.assertTrue(ok)
        mock_pdf.assert_not_called()
        mock_pdf_from_tgz.assert_called_once()

    @patch("downloader.pdf_downloader.download_pdf_from_tgz")
    @patch("downloader.pdf_downloader.download_pdf_file")
    def test_should_extract_from_tgz_when_pdf_direct_download_fails(self, mock_pdf, mock_pdf_from_tgz):
        mock_pdf.return_value = False
        mock_pdf_from_tgz.return_value = True

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ok = download_oa_pdf(
                links={"pdf": "https://example.org/a.pdf", "tgz": "https://example.org/a.tgz"},
                pdf_path=base / "a.pdf",
            )

        self.assertTrue(ok)
        mock_pdf.assert_called_once()
        mock_pdf_from_tgz.assert_called_once()

    @patch("downloader.pdf_downloader.download_pdf_from_tgz")
    @patch("downloader.pdf_downloader.download_pdf_file")
    def test_should_fail_when_pdf_unavailable(self, mock_pdf, mock_pdf_from_tgz):
        mock_pdf.return_value = False
        mock_pdf_from_tgz.return_value = False

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ok = download_oa_pdf(
                links={"pdf": "https://example.org/a.pdf", "tgz": "https://example.org/a.tgz"},
                pdf_path=base / "a.pdf",
            )

        self.assertFalse(ok)
        mock_pdf.assert_called_once()
        mock_pdf_from_tgz.assert_called_once()


class TestAria2Download(unittest.TestCase):
    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_download_pdf_file_via_aria2c(self, mock_run):
        pdf_payload = b"%PDF-1.4\n" + (b"B" * 3000)

        def fake_run(cmd, capture_output=True, text=True, timeout=300, check=False):
            out_dir = Path(cmd[cmd.index("-d") + 1])
            out_name = cmd[cmd.index("-o") + 1]
            out_path = out_dir / out_name
            out_path.write_bytes(pdf_payload)
            return Mock(returncode=0, stderr="", stdout="")

        mock_run.side_effect = fake_run

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "paper.pdf"
            ok = download_pdf_file("https://example.org/paper.pdf", dest)

            self.assertTrue(ok)
            self.assertTrue(dest.exists())
            self.assertGreater(dest.stat().st_size, 1024)

        self.assertTrue(mock_run.called)

    @patch("downloader.pdf_downloader.subprocess.run", side_effect=FileNotFoundError())
    def test_should_fail_when_aria2c_not_installed(self, _mock_run):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "paper.pdf"
            ok = download_pdf_file("https://example.org/paper.pdf", dest)

        self.assertFalse(ok)

    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_clean_aria2_control_file_on_failure(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stderr="network error", stdout="")

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "paper.pdf"
            control = Path(td) / "paper.pdf.part.aria2"
            control.write_bytes(b"\x00" * 64)

            ok = download_pdf_file("https://example.org/paper.pdf", dest)

            self.assertFalse(ok)
            self.assertFalse(control.exists())
            self.assertFalse((Path(td) / "paper.pdf.part").exists())


class TestAria2Proxy(unittest.TestCase):
    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_append_proxy_arg_when_configured(self, mock_run):
        payload = b"%PDF-1.4\n" + (b"B" * 3000)

        def fake_run(cmd, capture_output=True, text=True, timeout=300, check=False):
            out_dir = Path(cmd[cmd.index("-d") + 1])
            out_name = cmd[cmd.index("-o") + 1]
            (out_dir / out_name).write_bytes(payload)
            return Mock(returncode=0, stderr="", stdout="")

        mock_run.side_effect = fake_run
        with tempfile.TemporaryDirectory() as td:
            with patch("downloader.pdf_downloader.PROXY", "http://127.0.0.1:7890"):
                ok = download_pdf_file("https://example.org/1.pdf", Path(td) / "1.pdf")

        self.assertTrue(ok)
        called_cmd = mock_run.call_args.args[0]
        self.assertIn("--all-proxy=http://127.0.0.1:7890", called_cmd)

    @patch("downloader.pdf_downloader.subprocess.run")
    def test_should_not_append_proxy_arg_when_not_configured(self, mock_run):
        payload = b"%PDF-1.4\n" + (b"B" * 3000)

        def fake_run(cmd, capture_output=True, text=True, timeout=300, check=False):
            out_dir = Path(cmd[cmd.index("-d") + 1])
            out_name = cmd[cmd.index("-o") + 1]
            (out_dir / out_name).write_bytes(payload)
            return Mock(returncode=0, stderr="", stdout="")

        mock_run.side_effect = fake_run
        with tempfile.TemporaryDirectory() as td:
            with patch("downloader.pdf_downloader.PROXY", None):
                ok = download_pdf_file("https://example.org/2.pdf", Path(td) / "2.pdf")

        self.assertTrue(ok)
        called_cmd = mock_run.call_args.args[0]
        self.assertNotIn("--all-proxy", " ".join(called_cmd))


class TestFetchOaLinksProxy(unittest.TestCase):
    @patch("downloader.pdf_downloader.time.sleep")
    @patch("downloader.pdf_downloader.requests.get")
    def test_should_pass_proxies_to_requests_when_configured(self, mock_get, _sleep):
        captured = {}

        def fake_get(url, **kwargs):
            captured.update(kwargs)
            resp = Mock()
            resp.status_code = 200
            resp.content = (
                "<OA><records><record id='PMC123'>"
                "<link format='pdf' href='https://example.org/1.pdf'/>"
                "</record></records></OA>"
            ).encode()
            resp.raise_for_status = Mock()
            return resp

        mock_get.side_effect = fake_get
        with patch("downloader.pdf_downloader.PROXY", "http://127.0.0.1:7890"):
            result, _failed = fetch_oa_links(["PMC123"])

        self.assertIn("PMC123", result)
        self.assertEqual(
            captured["proxies"],
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        )

    @patch("downloader.pdf_downloader.time.sleep")
    @patch("downloader.pdf_downloader.requests.get")
    def test_should_not_pass_proxies_when_not_configured(self, mock_get, _sleep):
        captured = {}

        def fake_get(url, **kwargs):
            captured.update(kwargs)
            resp = Mock()
            resp.status_code = 200
            resp.content = (
                "<record><link format='pdf' href='https://example.org/2.pdf'/></record>"
            ).encode()
            resp.raise_for_status = Mock()
            return resp

        mock_get.side_effect = fake_get
        with patch("downloader.pdf_downloader.PROXY", None):
            fetch_oa_links(["PMC456"])

        self.assertNotIn("proxies", captured)


class TestFetchSingleOaLinkClassification(unittest.TestCase):
    def _resp(self, xml_text: str):
        resp = Mock()
        resp.status_code = 200
        resp.content = xml_text.encode("utf-8")
        resp.raise_for_status = Mock()
        return resp

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_ok_when_record_with_links(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = self._resp(
            "<OA><records><record id='PMC1'>"
            "<link format='pdf' href='https://a/1.pdf'/>"
            "<link format='tgz' href='https://a/1.tgz'/>"
            "</record></records></OA>"
        )

        pid, links, status = _fetch_single_oa_link("PMC1")

        self.assertEqual(pid, "PMC1")
        self.assertEqual(links, {"pdf": "https://a/1.pdf", "tgz": "https://a/1.tgz"})
        self.assertEqual(status, "ok")

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_not_oa_when_open_access_error(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = self._resp(
            "<OA><error code='idIsNotOpenAccess'>PMC1 is not Open Access</error></OA>"
        )
        pid, links, status = _fetch_single_oa_link("PMC1")
        self.assertEqual(status, "not_oa")
        self.assertIsNone(links)

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_not_oa_when_id_does_not_exist(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = self._resp(
            "<OA><error code='idDoesNotExist'>PMC9 does not exist</error></OA>"
        )
        pid, links, status = _fetch_single_oa_link("PMC9")
        self.assertEqual(status, "not_oa")
        self.assertIsNone(links)

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_network_fail_when_request_failed(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = None
        pid, links, status = _fetch_single_oa_link("PMC2")
        self.assertEqual(status, "network_fail")
        self.assertIsNone(links)

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_network_fail_when_no_record_no_error(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = self._resp("<OA></OA>")
        pid, links, status = _fetch_single_oa_link("PMC3")
        self.assertEqual(status, "network_fail")
        self.assertIsNone(links)


class TestFetchOaLinksClassification(unittest.TestCase):
    @patch("downloader.pdf_downloader.time.sleep")
    @patch("downloader.pdf_downloader.requests.get")
    def test_should_return_network_failed_ids_and_exclude_not_oa(self, mock_get, _sleep):
        captured = {}

        def fake_get(url, **kwargs):
            captured.setdefault("count", 0)
            captured["count"] += 1
            pid = (kwargs.get("params") or {}).get("id")
            resp = Mock()
            resp.status_code = 200
            resp.raise_for_status = Mock()
            if pid == "PMC_OK":
                resp.content = (
                    "<record><link format='pdf' href='https://a/ok.pdf'/></record>"
                ).encode("utf-8")
            elif pid == "PMC_NOT_OA":
                resp.content = (
                    "<OA><error code='idIsNotOpenAccess'>x</error></OA>"
                ).encode("utf-8")
            else:
                resp.content = b"<OA></OA>"
            return resp

        mock_get.side_effect = fake_get

        result, failed = fetch_oa_links(["PMC_OK", "PMC_NOT_OA", "PMC_NET"])

        self.assertIn("PMC_OK", result)
        self.assertNotIn("PMC_NOT_OA", result)
        self.assertEqual(failed, ["PMC_NET"])
        self.assertEqual(captured["count"], 3)


class TestExportOaLinksCsv(unittest.TestCase):
    def test_should_export_required_columns_and_rows(self):
        oa_links = {
            "PMC1": {"pdf": "https://example.org/1.pdf"},
            "PMC2": {"tgz": "https://example.org/2.tgz"},
        }
        pmc_to_info = {
            "PMC1": {"pmid": "111"},
            "PMC2": {"pmid": "222"},
        }

        with tempfile.TemporaryDirectory() as td:
            out_path = export_oa_links_csv(oa_links, pmc_to_info, Path(td))
            self.assertTrue(out_path.exists())

            with open(out_path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["pmid"], "111")
        self.assertEqual(rows[0]["pmc_id"], "PMC1")
        
        self.assertEqual(rows[0]["pdf_url"], "https://example.org/1.pdf")
        self.assertEqual(rows[1]["tgz_url"], "https://example.org/2.tgz")


class TestPdfCheckpoint(unittest.TestCase):
    def test_should_save_and_load_pending(self):
        from downloader.pdf_downloader import (
            _save_pdf_checkpoint, _load_pdf_checkpoint, _clear_pdf_checkpoint,
        )

        pending = [
            {"pmid": "111", "pmc_id": "PMC1", "links": {"pdf": "https://a/1.pdf"}},
            {"pmid": "112", "pmc_id": "PMC2", "links": {}},
        ]

        with tempfile.TemporaryDirectory() as td:
            with patch("downloader.pdf_downloader.OUTPUT_DIR", Path(td)):
                _save_pdf_checkpoint(pending)
                self.assertEqual(_load_pdf_checkpoint(), pending)
                _clear_pdf_checkpoint()
                self.assertEqual(_load_pdf_checkpoint(), [])

    def test_should_return_empty_when_checkpoint_missing(self):
        from downloader.pdf_downloader import _load_pdf_checkpoint

        with tempfile.TemporaryDirectory() as td:
            with patch("downloader.pdf_downloader.OUTPUT_DIR", Path(td)):
                self.assertEqual(_load_pdf_checkpoint(), [])

    def test_should_return_empty_when_checkpoint_corrupted(self):
        from downloader.pdf_downloader import (
            _pdf_checkpoint_path, _load_pdf_checkpoint,
        )

        with tempfile.TemporaryDirectory() as td:
            with patch("downloader.pdf_downloader.OUTPUT_DIR", Path(td)):
                path = _pdf_checkpoint_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{not valid json", encoding="utf-8")
                self.assertEqual(_load_pdf_checkpoint(), [])


class TestLoadFailedItemsFromCsv(unittest.TestCase):
    def test_should_parse_latest_failed_csv(self):
        from downloader.pdf_downloader import load_failed_items_from_csv

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            old = out_dir / "failed_downloads_20260101_010101.csv"
            new = out_dir / "failed_downloads_20260102_010101.csv"
            old.write_text(
                'pmid,pmc_id,pdf_url,tgz_url\n111,PMC1,https://old/1.pdf,\n',
                encoding="utf-8-sig",
            )
            new.write_text(
                'pmid,pmc_id,pdf_url,tgz_url\n'
                '222,PMC2,,\n'
                '333,pmc3,ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/a/b/3.PMC3.pdf,\n',
                encoding="utf-8-sig",
            )

            items = load_failed_items_from_csv(out_dir=out_dir)

        self.assertEqual(
            items,
            [
                {"pmid": "222", "pmc_id": "PMC2", "links": {}},
                {
                    "pmid": "333",
                    "pmc_id": "PMC3",
                    "links": {
                        "pdf": "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/oa_pdf/a/b/3.PMC3.pdf",
                    },
                },
            ],
        )

    def test_should_parse_tgz_url(self):
        from downloader.pdf_downloader import load_failed_items_from_csv

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "failed_downloads_20260101_010101.csv").write_text(
                "pmid,pmc_id,pdf_url,tgz_url\n"
                "444,PMC4,,ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/a/b/4.PMC4.tar.gz\n",
                encoding="utf-8-sig",
            )

            items = load_failed_items_from_csv(out_dir=out_dir)

        self.assertEqual(
            items,
            [
                {
                    "pmid": "444",
                    "pmc_id": "PMC4",
                    "links": {
                        "tgz": "https://ftp.ncbi.nlm.nih.gov/pub/pmc/deprecated/oa_package/a/b/4.PMC4.tar.gz",
                    },
                },
            ],
        )

    def test_should_return_empty_when_no_csv(self):
        from downloader.pdf_downloader import load_failed_items_from_csv

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_failed_items_from_csv(out_dir=Path(td)), [])

    def test_should_return_empty_when_csv_corrupted(self):
        from downloader.pdf_downloader import load_failed_items_from_csv

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "failed_downloads_20260101_010101.csv").write_bytes(
                b"pmid,pmc_id,pdf_url,tgz_url\n\xc3\x28",
            )
            self.assertEqual(load_failed_items_from_csv(out_dir=out_dir), [])


class TestRunPdfRetry(unittest.TestCase):
    @patch("downloader.pdf_downloader._load_pdf_checkpoint")
    @patch("downloader.pdf_downloader.load_failed_items_from_csv")
    @patch("downloader.pdf_downloader.fetch_oa_links")
    @patch("downloader.pdf_downloader.download_oa_pdf")
    @patch("downloader.pdf_downloader.export_failed_links_csv")
    @patch("downloader.pdf_downloader._clear_pdf_checkpoint")
    def test_should_resume_from_checkpoint_and_clear_on_success(
        self, mock_clear, mock_export_csv, mock_dl, mock_fetch,
        mock_csv, mock_checkpoint,
    ):
        mock_dl.return_value = True
        mock_fetch.return_value = ({}, [])
        with tempfile.TemporaryDirectory() as td:
            pdf_dir = Path(td)
            (pdf_dir / "222.pdf").write_bytes(b"%PDF-1.4\n" + b"A" * 2000)
            mock_checkpoint.return_value = [
                {"pmid": "111", "pmc_id": "PMC1", "links": {}},
                {"pmid": "222", "pmc_id": "PMC2", "links": {"pdf": "https://a/2.pdf"}},
            ]
            with patch("downloader.pdf_downloader.PDF_DIR", pdf_dir):
                from downloader.pdf_downloader import run_pdf_retry
                run_pdf_retry()

        mock_csv.assert_not_called()          # 有 checkpoint 不读 CSV
        mock_fetch.assert_called_once()       # 仅 PMC1 无链接需重查
        mock_dl.assert_called_once()          # 222.pdf 已存在被过滤，仅 111 需下载
        mock_clear.assert_called_once()       # 全部处理后清除

    @patch("downloader.pdf_downloader._load_pdf_checkpoint")
    @patch("downloader.pdf_downloader.load_failed_items_from_csv")
    @patch("downloader.pdf_downloader.fetch_oa_links")
    @patch("downloader.pdf_downloader.download_oa_pdf")
    @patch("downloader.pdf_downloader.export_failed_links_csv")
    @patch("downloader.pdf_downloader._save_pdf_checkpoint")
    @patch("downloader.pdf_downloader._clear_pdf_checkpoint")
    def test_should_keep_checkpoint_when_partial_failure(
        self, mock_clear, mock_save, mock_export, mock_dl, mock_fetch, mock_csv, mock_checkpoint,
    ):
        mock_checkpoint.return_value = []
        mock_csv.return_value = [
            {"pmid": "333", "pmc_id": "PMC3", "links": {"pdf": "https://a/3.pdf"}},
            {"pmid": "444", "pmc_id": "PMC4", "links": {"pdf": "https://a/4.pdf"}},
        ]
        mock_fetch.return_value = ({}, [])
        mock_dl.return_value = False
        mock_export.return_value = Path("failed_downloads_x.csv")
        with tempfile.TemporaryDirectory() as td:
            with patch("downloader.pdf_downloader.PDF_DIR", Path(td)):
                from downloader.pdf_downloader import run_pdf_retry
                run_pdf_retry()

        mock_save.assert_called()
        mock_clear.assert_not_called()
        mock_export.assert_called_once()

    @patch("downloader.pdf_downloader._load_pdf_checkpoint")
    @patch("downloader.pdf_downloader.load_failed_items_from_csv")
    @patch("downloader.pdf_downloader.fetch_oa_links")
    @patch("downloader.pdf_downloader.download_oa_pdf")
    @patch("downloader.pdf_downloader.export_failed_links_csv")
    @patch("downloader.pdf_downloader._save_pdf_checkpoint")
    @patch("downloader.pdf_downloader._clear_pdf_checkpoint")
    def test_should_snapshot_only_unfinished_items(
        self, mock_clear, mock_save, mock_export, mock_dl, mock_fetch, mock_csv, mock_checkpoint,
    ):
        mock_checkpoint.return_value = []
        mock_csv.return_value = [
            {"pmid": str(i), "pmc_id": f"PMC{i}", "links": {"pdf": f"https://a/{i}.pdf"}}
            for i in range(1, 16)
        ]
        mock_fetch.return_value = ({}, [])
        mock_dl.side_effect = lambda links, pdf_path: Path(pdf_path).name in {
            f"{i}.pdf" for i in range(1, 11)
        }
        mock_export.return_value = Path("failed_downloads_x.csv")
        with tempfile.TemporaryDirectory() as td:
            with patch("downloader.pdf_downloader.PDF_DIR", Path(td)):
                from downloader.pdf_downloader import run_pdf_retry
                run_pdf_retry()

        saved_snapshots = [call.args[0] for call in mock_save.call_args_list]
        self.assertTrue(saved_snapshots)
        for snapshot in saved_snapshots:
            snapshot_pmids = {item["pmid"] for item in snapshot}
            self.assertFalse(snapshot_pmids & {str(i) for i in range(1, 11)})
            self.assertTrue(snapshot_pmids)
        self.assertEqual(
            {item["pmid"] for item in saved_snapshots[-1]},
            {str(i) for i in range(11, 16)},
        )


class TestRunPdfWriteCheckpoint(unittest.TestCase):
    def _make_db_with_pmc(self, td: Path, pmid: str = "1111", pmc_id: str = "PMC1") -> Path:
        import sqlite3
        db = td / "test.db"
        conn = sqlite3.connect(db)
        conn.execute(CREATE_ARTICLES_SQL)
        conn.execute(CREATE_LLM_VALIDATION_SQL)
        conn.execute(
            "INSERT INTO articles (pmid, pmc_id) VALUES (?, ?)", (pmid, pmc_id)
        )
        conn.execute(
            "INSERT INTO llm_validation (pmid, llm_verdict) VALUES (?, 'RELEVANT')",
            (pmid,),
        )
        conn.commit()
        conn.close()
        return db

    @patch("downloader.pdf_downloader.fetch_oa_links")
    @patch("downloader.pdf_downloader.download_oa_pdf")
    @patch("downloader.pdf_downloader.load_cached_oa_links")
    @patch("downloader.pdf_downloader.export_oa_links_csv")
    def test_should_write_checkpoint_on_failure(
        self, mock_export_csv, mock_cached, mock_dl, mock_fetch,
    ):
        mock_fetch.return_value = ({"PMC1": {"pdf": "https://a/1.pdf"}}, [])
        mock_dl.return_value = False
        mock_export_csv.return_value = Path("links.csv")
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            db = self._make_db_with_pmc(td_path)
            with patch("downloader.pdf_downloader.PDF_DIR", td_path):
                with patch("downloader.pdf_downloader.OUTPUT_DIR", td_path):
                    from downloader.pdf_downloader import (
                        run_pdf_download, _load_pdf_checkpoint, _pdf_checkpoint_path,
                    )
                    run_pdf_download(db_path=db)

                    checkpoint_items = _load_pdf_checkpoint()
                    checkpoint_path = _pdf_checkpoint_path()

                    self.assertTrue(checkpoint_path.exists())
                    self.assertEqual(len(checkpoint_items), 1)
                    self.assertEqual(checkpoint_items[0]["pmid"], "1111")
                    self.assertEqual(checkpoint_items[0]["pmc_id"], "PMC1")
                    self.assertEqual(checkpoint_items[0]["links"], {"pdf": "https://a/1.pdf"})
                    self.assertNotIn("pdf_path", checkpoint_items[0])

    @patch("downloader.pdf_downloader.fetch_oa_links")
    @patch("downloader.pdf_downloader.download_oa_pdf")
    @patch("downloader.pdf_downloader.load_cached_oa_links")
    @patch("downloader.pdf_downloader.export_oa_links_csv")
    def test_should_clear_checkpoint_on_success(
        self, mock_export_csv, mock_cached, mock_dl, mock_fetch,
    ):
        mock_fetch.return_value = ({"PMC1": {"pdf": "https://a/1.pdf"}}, [])
        mock_dl.return_value = True
        mock_export_csv.return_value = Path("links.csv")
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            db = self._make_db_with_pmc(td_path)
            with patch("downloader.pdf_downloader.PDF_DIR", td_path):
                with patch("downloader.pdf_downloader.OUTPUT_DIR", td_path):
                    from downloader.pdf_downloader import (
                        run_pdf_download, _save_pdf_checkpoint, _pdf_checkpoint_path,
                    )
                    _save_pdf_checkpoint([{"pmid": "9999", "pmc_id": "PMC9", "links": {}}])
                    run_pdf_download(db_path=db)

                    checkpoint_exists = _pdf_checkpoint_path().exists()

        self.assertFalse(checkpoint_exists)

    @patch("downloader.pdf_downloader.fetch_oa_links")
    @patch("downloader.pdf_downloader.load_cached_oa_links")
    @patch("downloader.pdf_downloader.export_oa_links_csv")
    def test_should_keep_checkpoint_when_no_task(
        self, mock_export_csv, mock_cached, mock_fetch,
    ):
        mock_fetch.return_value = ({}, [])
        mock_export_csv.return_value = Path("links.csv")
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            db = self._make_db_with_pmc(td_path)
            with patch("downloader.pdf_downloader.PDF_DIR", td_path):
                with patch("downloader.pdf_downloader.OUTPUT_DIR", td_path):
                    from downloader.pdf_downloader import (
                        run_pdf_download, _save_pdf_checkpoint, _pdf_checkpoint_path,
                    )
                    _save_pdf_checkpoint([{"pmid": "9999", "pmc_id": "PMC9", "links": {}}])
                    run_pdf_download(db_path=db)

                    checkpoint_exists = _pdf_checkpoint_path().exists()

        self.assertTrue(checkpoint_exists)


if __name__ == "__main__":
    unittest.main()
