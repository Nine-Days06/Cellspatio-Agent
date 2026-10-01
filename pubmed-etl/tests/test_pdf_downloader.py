import unittest
from unittest.mock import patch, Mock
import io
import json
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

# PMC Cloud Service on AWS（OA 链接解析通道）的测试夹具。
# 2026-08 起 NCBI OA Web Service(oa.fcgi) 永久下线，链接改由 S3 桶解析：
#   1) GET {bucket}?list-type=2&prefix={PMCID}.&delimiter=/  取版本目录（XML）
#   2) GET {bucket}/{PMCID}.{ver}/{PMCID}.{ver}.json      取元数据（JSON）
S3_XML_NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def s3_list_resp(prefixes=(), next_token="", status=200):
    """构造 S3 ListObjectsV2 响应；prefixes 为 CommonPrefixes 原始字符串列表。"""
    items = "".join(
        f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>" for p in prefixes
    )
    token = (
        f"<NextContinuationToken>{next_token}</NextContinuationToken>" if next_token else ""
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<ListBucketResult xmlns="{S3_XML_NS}">'
        f"<Name>pmc-oa-opendata</Name>"
        f"<IsTruncated>{'true' if next_token else 'false'}</IsTruncated>"
        f"{items}{token}"
        f"</ListBucketResult>"
    )
    resp = Mock()
    resp.status_code = status
    resp.content = xml.encode("utf-8")
    resp.raise_for_status = Mock()
    return resp


def s3_meta_resp(payload, status=200):
    """构造 S3 云元数据 JSON 响应。"""
    resp = Mock()
    resp.status_code = status
    resp.json = Mock(return_value=payload)
    resp.content = json.dumps(payload).encode("utf-8")
    resp.raise_for_status = Mock()
    return resp


def s3_meta(pdf=None, txt=None, is_manuscript=False, pmc_id="PMC1", ver=1):
    """按需拼一份云元数据字典（URL 保持 s3:// 形态，由被测函数负责转换）。"""
    data = {"pmcid": pmc_id, "version": ver, "is_manuscript": is_manuscript}
    if pdf:
        data["pdf_url"] = f"s3://pmc-oa-opendata/{pmc_id}.{ver}/{pmc_id}.{ver}.pdf?md5=deadbeef"
    if txt:
        data["text_url"] = f"s3://pmc-oa-opendata/{pmc_id}.{ver}/{pmc_id}.{ver}.txt?md5=deadbeef"
    return data


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
    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_resolve_pdf_and_txt_links_via_s3(self, mock_req, _mock_sleep):
        """S3 通道：桶列举拿到版本 → 元数据拿到 pdf/txt → 链接转 HTTPS。"""
        seen_prefixes = []

        def fake_req(url, params=None, **kwargs):
            prefix = (params or {}).get("prefix")
            if prefix is not None:
                seen_prefixes.append(prefix)
                if prefix == "PMC4334330.":
                    return s3_list_resp(["PMC4334330.1/"])
                return s3_list_resp([])  # 桶中无该 PMCID 目录 → 非 OA
            if url.endswith(".json"):
                return s3_meta_resp(s3_meta(pdf=True, txt=True, pmc_id="PMC4334330"))
            raise AssertionError(f"意外请求: {url} {params}")

        mock_req.side_effect = fake_req

        result, failed = fetch_oa_links(["PMC4334330", "PMC9999999"])

        self.assertIn("PMC4334330", result)
        self.assertEqual(
            result["PMC4334330"]["pdf"],
            "https://pmc-oa-opendata.s3.amazonaws.com/"
            "PMC4334330.1/PMC4334330.1.pdf?md5=deadbeef",
        )
        self.assertEqual(
            result["PMC4334330"]["txt"],
            "https://pmc-oa-opendata.s3.amazonaws.com/"
            "PMC4334330.1/PMC4334330.1.txt?md5=deadbeef",
        )
        # 桶中无目录 → 静默跳过，不算网络失败
        self.assertNotIn("PMC9999999", result)
        self.assertEqual(failed, [])
        self.assertIn("PMC4334330.", seen_prefixes)
        self.assertIn("PMC9999999.", seen_prefixes)

    @patch("downloader.pdf_downloader.time.sleep")
    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_cost_two_requests_per_article(self, mock_req, _mock_sleep):
        """每篇固定 2 个请求：1 次版本列举 + 1 次元数据，不再有批量端点与二次补查。"""

        def fake_req(url, params=None, **kwargs):
            prefix = (params or {}).get("prefix")
            if prefix == "PMC1000001.":
                return s3_list_resp(["PMC1000001.1/"])
            if prefix == "PMC1000002.":
                return s3_list_resp(["PMC1000002.2/"])
            return s3_meta_resp(s3_meta(pdf=True, pmc_id="PMC1000002", ver=2))

        mock_req.side_effect = fake_req

        result, failed = fetch_oa_links(["PMC1000001", "PMC1000002"])

        self.assertIn("PMC1000001", result)
        self.assertIn("PMC1000002", result)
        self.assertEqual(failed, [])
        # 2 篇 × (列举 + 元数据) = 4
        self.assertEqual(mock_req.call_count, 4)

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

    def test_should_drop_dead_ftp_links_from_cache(self):
        """NCBI FTP 路径随 OA Web Service 一起删除，缓存命中也下不动，必须丢弃重新解析。"""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "oa_download_links_20260101_010101.csv").write_text(
                "pmid,pmc_id,label,pdf_url,tgz_url\n"
                "111,PMC1,高相关,ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/a/b/1.pdf,\n"
                "222,PMC2,中相关,,https://ok.example/2.tgz\n"
                "333,PMC3,低相关,https://ok.example/3.pdf,"
                "ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/a/b/3.tgz\n"
                "444,PMC4,高相关,ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/a/b/4.pdf,\n",
                encoding="utf-8",
            )

            cached = load_cached_oa_links(
                ["PMC1", "PMC2", "PMC3", "PMC4"], out_dir=out_dir
            )

        # 整条记录只有死链 → 不入缓存，交由 S3 重新解析
        self.assertNotIn("PMC1", cached)
        self.assertNotIn("PMC4", cached)
        # 死链字段被剔除，存活字段仍可用（活 PDF 保留，死 tgz 丢弃）
        self.assertEqual(cached["PMC3"]["pdf"], "https://ok.example/3.pdf")
        self.assertNotIn("tgz", cached["PMC3"])
        self.assertEqual(cached["PMC2"]["tgz"], "https://ok.example/2.tgz")

    def test_should_keep_https_s3_links_in_cache(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "oa_download_links_20260101_010101.csv").write_text(
                "pmid,pmc_id,label,pdf_url,tgz_url\n"
                "111,PMC1,高相关,https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.pdf,\n",
                encoding="utf-8",
            )

            cached = load_cached_oa_links(["PMC1"], out_dir=out_dir)

        self.assertEqual(
            cached["PMC1"]["pdf"],
            "https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.pdf",
        )


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
            if (kwargs.get("params") or {}).get("prefix") is not None:
                return s3_list_resp(["PMC123.1/"])
            return s3_meta_resp(s3_meta(pdf=True, pmc_id="PMC123"))

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


class TestListS3Versions(unittest.TestCase):
    """版本列举的解析规则：严格正则挡前缀碰撞与非法 prefix，跟随分页。"""

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_extract_sorted_unique_versions(self, mock_req):
        from downloader.pdf_downloader import _list_s3_versions

        mock_req.return_value = s3_list_resp(["PMC7.2/", "PMC7.1/", "PMC7.1/"])
        self.assertEqual(_list_s3_versions("PMC7"), [1, 2])

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_ignore_longer_pmc_id_sharing_numeric_prefix(self, mock_req):
        """PMC7 的列举里若混入 PMC7777/ 这类更长 ID，不得被误认作 PMC7 的版本。"""
        from downloader.pdf_downloader import _list_s3_versions

        mock_req.return_value = s3_list_resp(["PMC7.1/", "PMC7777.3/", "PMC70.2/"])
        self.assertEqual(_list_s3_versions("PMC7"), [1])

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_ignore_malformed_prefixes(self, mock_req):
        """非 {PMCID}.{数字}/ 形状的 prefix 一律忽略。

        这里刻意不放任何合法 prefix，使"漏掉尾部 /$ 校验"的实现会立刻暴露
        （宽松实现会把 PMC8.1/extra/、PMC8.1 误判为版本 1，严格实现返回空）。
        """
        from downloader.pdf_downloader import _list_s3_versions

        mock_req.return_value = s3_list_resp(
            [
                "PMC8.txt",          # 版本段非数字
                "PMC8.1",            # 缺尾部斜杠
                "PMC8.1/extra/",     # 斜杠后多一层
                "other/PMC8.1/",     # 前缀顺序不对
                "PMC81/",            # 缺小数点
                " PMC8.1 /",         # 带空白
            ]
        )
        self.assertEqual(_list_s3_versions("PMC8"), [])

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_follow_continuation_token(self, mock_req):
        from downloader.pdf_downloader import _list_s3_versions

        pages = [
            s3_list_resp(["PMC9.1/"], next_token="tok-1"),
            s3_list_resp(["PMC9.2/"]),
        ]
        mock_req.side_effect = pages

        self.assertEqual(_list_s3_versions("PMC9"), [1, 2])
        self.assertEqual(mock_req.call_count, 2)
        # 第二页必须带上上一页的 continuation-token
        second_params = mock_req.call_args_list[1].kwargs["params"]
        self.assertEqual(second_params.get("continuation-token"), "tok-1")
        self.assertEqual(second_params.get("prefix"), "PMC9.")

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_return_empty_list_on_404(self, mock_req):
        """404 = 桶中无该 PMCID 目录 → 视为非 OA，不算传输失败。"""
        from downloader.pdf_downloader import _list_s3_versions

        mock_req.return_value = s3_list_resp([], status=404)
        self.assertEqual(_list_s3_versions("PMC404"), [])

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_raise_transport_error_when_request_returns_none(self, mock_req):
        from downloader.pdf_downloader import OATransportError, _list_s3_versions

        mock_req.return_value = None
        with self.assertRaises(OATransportError):
            _list_s3_versions("PMC0")


class TestS3UriToHttps(unittest.TestCase):
    def test_should_convert_s3_uri_to_anonymous_https(self):
        from downloader.pdf_downloader import _s3_uri_to_https

        self.assertEqual(
            _s3_uri_to_https("s3://pmc-oa-opendata/PMC1.1/PMC1.1.pdf?md5=abc"),
            "https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.pdf?md5=abc",
        )

    def test_should_pass_through_non_s3_input(self):
        from downloader.pdf_downloader import _s3_uri_to_https

        self.assertEqual(
            _s3_uri_to_https("https://example.org/1.pdf"), "https://example.org/1.pdf"
        )
        self.assertEqual(_s3_uri_to_https(""), "")


class TestFetchSingleOaLinkClassification(unittest.TestCase):
    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_ok_and_return_pdf_and_txt(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        def fake_req(url, params=None, **kwargs):
            if (params or {}).get("prefix") is not None:
                return s3_list_resp(["PMC1.1/"])
            return s3_meta_resp(s3_meta(pdf=True, txt=True, pmc_id="PMC1"))

        mock_req.side_effect = fake_req

        pid, links, status = _fetch_single_oa_link("PMC1")

        self.assertEqual(pid, "PMC1")
        self.assertEqual(status, "ok")
        self.assertEqual(links["pdf"], f"https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.pdf?md5=deadbeef")
        self.assertEqual(links["txt"], f"https://pmc-oa-opendata.s3.amazonaws.com/PMC1.1/PMC1.1.txt?md5=deadbeef")

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_prefer_non_manuscript_pdf_over_manuscript(self, mock_req):
        """非作者手稿 PDF 优先于手稿 PDF；手稿 PDF 优先于仅文本。"""
        from downloader.pdf_downloader import _fetch_single_oa_link

        def fake_req(url, params=None, **kwargs):
            if (params or {}).get("prefix") is not None:
                return s3_list_resp(["PMC2.1/", "PMC2.2/"])
            ver = 1 if url.endswith("PMC2.1.json") else 2
            return s3_meta_resp(
                s3_meta(pdf=True, is_manuscript=True, pmc_id="PMC2", ver=ver)
            )

        mock_req.side_effect = fake_req

        _pid, links, status = _fetch_single_oa_link("PMC2")

        self.assertEqual(status, "ok")
        self.assertIn(".2.pdf", links["pdf"])

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_fall_back_to_txt_when_no_pdf(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        def fake_req(url, params=None, **kwargs):
            if (params or {}).get("prefix") is not None:
                return s3_list_resp(["PMC3.1/"])
            return s3_meta_resp(s3_meta(pdf=False, txt=True, pmc_id="PMC3"))

        mock_req.side_effect = fake_req

        _pid, links, status = _fetch_single_oa_link("PMC3")

        self.assertEqual(status, "ok")
        self.assertNotIn("pdf", links)
        self.assertIn("txt", links)

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_not_oa_when_no_versions(self, mock_req):
        """空列举 = 非 OA，静默跳过（旧实现会判成 network_fail，是已修的分类缺陷）。"""
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = s3_list_resp([])
        pid, links, status = _fetch_single_oa_link("PMC4")
        self.assertEqual(pid, "PMC4")
        self.assertEqual(status, "not_oa")
        self.assertIsNone(links)

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_not_oa_when_list_404(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = s3_list_resp([], status=404)
        pid, links, status = _fetch_single_oa_link("PMC5")
        self.assertEqual(status, "not_oa")
        self.assertIsNone(links)

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_not_oa_when_all_metadata_empty(self, mock_req):
        """版本目录存在但元数据里既无 pdf 也无 text → 仍判非 OA。"""
        from downloader.pdf_downloader import _fetch_single_oa_link

        def fake_req(url, params=None, **kwargs):
            if (params or {}).get("prefix") is not None:
                return s3_list_resp(["PMC6.1/"])
            return s3_meta_resp({"pmcid": "PMC6", "version": 1})

        mock_req.side_effect = fake_req

        _pid, links, status = _fetch_single_oa_link("PMC6")
        self.assertEqual(status, "not_oa")
        self.assertIsNone(links)

    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_classify_network_fail_when_request_failed(self, mock_req):
        from downloader.pdf_downloader import _fetch_single_oa_link

        mock_req.return_value = None
        pid, links, status = _fetch_single_oa_link("PMC0")
        self.assertEqual(pid, "PMC0")
        self.assertEqual(status, "network_fail")
        self.assertIsNone(links)


class TestFetchOaLinksClassification(unittest.TestCase):
    @patch("downloader.pdf_downloader.time.sleep")
    @patch("downloader.pdf_downloader._request_oa_with_retry")
    def test_should_return_network_failed_ids_and_exclude_not_oa(self, mock_req, _sleep):
        """三类分流：解析成功入 result、无版本目录静默跳过、真传输失败入 failed。"""

        def fake_req(url, params=None, **kwargs):
            prefix = (params or {}).get("prefix")
            if prefix == "PMC100.":
                return s3_list_resp(["PMC100.1/"])
            if prefix == "PMC200.":
                return s3_list_resp([])  # 非 OA
            if prefix == "PMC300.":
                return None            # 传输失败（重试耗尽）
            return s3_meta_resp(s3_meta(pdf=True, pmc_id="PMC100"))

        mock_req.side_effect = fake_req

        result, failed = fetch_oa_links(["PMC100", "PMC200", "PMC300"])

        self.assertIn("PMC100", result)
        self.assertNotIn("PMC200", result)
        # 并发解析，failed 顺序不确定
        self.assertCountEqual(failed, ["PMC300"])


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


class TestRunPdfDownloadSkipsExistingFulltext(unittest.TestCase):
    """P1 回归：链接解析失败 ≠ 下载失败。

    取链整体失败时 oa_links 为空（旧实现里 oa.fcgi 退役期间正是如此），
    "已下载则跳过" 的判断因位于遍历循环体内而永不执行，导致本地已有全文的
    文献被写进失败清单与检查点，--step pdf-retry 无限重试。
    """

    def _make_db(self, td: Path, pmid: str, pmc_id: str) -> Path:
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

    def _run_with_network_failure(self, db: Path, td_path: Path):
        """模拟取链整体失败：oa_links 为空，network_failed 含该 PMCID。"""
        from downloader.pdf_downloader import (
            run_pdf_download, _load_pdf_checkpoint, _pdf_checkpoint_path,
        )

        with patch("downloader.pdf_downloader.PDF_DIR", td_path), patch(
            "downloader.pdf_downloader.OUTPUT_DIR", td_path
        ):
            run_pdf_download(db_path=db)
            return _load_pdf_checkpoint(), _pdf_checkpoint_path().exists()

    def _patches(self):
        """返回 5 个已 start 的 mock（须持有返回值，否则只能拿到 _patch 对象）。"""
        return (
            patch("downloader.pdf_downloader.load_cached_oa_links", return_value={}).start(),
            patch(
                "downloader.pdf_downloader.export_oa_links_csv",
                return_value=Path("links.csv"),
            ).start(),
            patch("downloader.pdf_downloader.fetch_oa_links", return_value=({}, ["PMC1"])).start(),
            patch("downloader.pdf_downloader.download_oa_pdf", return_value=False).start(),
            patch("downloader.pdf_downloader.export_failed_links_csv").start(),
        )

    def _stop(self, mocks):
        for m in mocks:
            patch.stopall()
        self.assertTrue(all(hasattr(m, "assert_not_called") for m in mocks))

    def test_should_not_mark_existing_pdf_as_failed(self):
        m_cached, m_export, m_fetch, m_dl, m_failcsv = self._patches()
        try:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                db = self._make_db(td_path, "1111", "PMC1")
                (td_path / "1111.pdf").write_bytes(b"%PDF-1.4\n" + b"A" * 2000)
                checkpoint, exists = self._run_with_network_failure(db, td_path)
        finally:
            self._stop((m_cached, m_export, m_fetch, m_dl, m_failcsv))

        self.assertEqual(checkpoint, [])          # 不进检查点
        self.assertFalse(exists)                  # 不写检查点文件
        m_failcsv.assert_not_called()             # 不出失败清单
        m_dl.assert_not_called()                  # 已存在，无需再下

    def test_should_not_mark_existing_txt_as_failed(self):
        m_cached, m_export, m_fetch, m_dl, m_failcsv = self._patches()
        try:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                db = self._make_db(td_path, "1111", "PMC1")
                # 仅存在 .txt（OA 包内无 PDF 时的回退产物）同样视为已下载
                (td_path / "1111.txt").write_text("full text", encoding="utf-8")
                checkpoint, exists = self._run_with_network_failure(db, td_path)
        finally:
            self._stop((m_cached, m_export, m_fetch, m_dl, m_failcsv))

        self.assertEqual(checkpoint, [])
        self.assertFalse(exists)
        m_failcsv.assert_not_called()
        m_dl.assert_not_called()

    def test_should_still_record_when_nothing_downloaded_locally(self):
        """对照组：本地确无全文时，仍须照常记入失败清单，否则真失败会被吞掉。"""
        m_cached, m_export, m_fetch, m_dl, m_failcsv = self._patches()
        m_failcsv.return_value = Path("failed.csv")
        try:
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                db = self._make_db(td_path, "1111", "PMC1")
                checkpoint, exists = self._run_with_network_failure(db, td_path)
        finally:
            self._stop((m_cached, m_export, m_fetch, m_dl, m_failcsv))

        self.assertTrue(exists)
        self.assertEqual([i["pmid"] for i in checkpoint], ["1111"])
        m_failcsv.assert_called_once()



if __name__ == "__main__":
    unittest.main()
