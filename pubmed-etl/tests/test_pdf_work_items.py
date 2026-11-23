import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from downloader.pdf_work_items import (
    PdfWorkItem,
    execute_pdf_work_items,
    load_pdf_work_checkpoint,
    save_pdf_work_checkpoint,
)


def item(pmid: str, pmc_id: str, links: dict[str, str] | None = None) -> PdfWorkItem:
    """构造 runner 测试工作项。"""
    return PdfWorkItem(pmid=pmid, pmc_id=pmc_id, links=links or {})


class TestExecutePdfWorkItems(unittest.TestCase):
    """统一 runner 的分类、重试和恢复快照。"""

    def test_classifies_existing_success_and_failure_in_input_order(self) -> None:
        """跳过既存全文并保持成功、失败的结果顺序。"""
        first = item("111", "PMC1", {"pdf": "https://a/1.pdf"})
        failed = item("222", "PMC2", {"pdf": "https://a/2.pdf"})
        existing = item("333", "PMC3", {"pdf": "https://a/3.pdf"})
        download = Mock(side_effect=lambda current: current != failed)

        result = execute_pdf_work_items(
            [first, failed, existing],
            automatic_retry_rounds=0,
            local_fulltext_exists=lambda current: current == existing,
            download=download,
        )

        self.assertEqual(result.succeeded, (first,))
        self.assertEqual(result.skipped, (existing,))
        self.assertEqual(result.failed, (failed,))
        self.assertEqual(download.call_count, 2)

    def test_initial_retries_only_failed_items_with_links(self) -> None:
        """初次运行只自动重试有链接的失败项。"""
        linked = item("111", "PMC1", {"pdf": "https://a/1.pdf"})
        no_link = item("222", "PMC2")
        calls: list[str] = []

        def download(current: PdfWorkItem) -> bool:
            """记录初次与自动重试的调用顺序。"""
            calls.append(current.pmid)
            return False

        result = execute_pdf_work_items(
            [linked, no_link],
            automatic_retry_rounds=2,
            local_fulltext_exists=lambda _current: False,
            download=download,
        )

        self.assertEqual(calls, ["111", "222", "111", "111"])
        self.assertEqual(result.failed, (linked, no_link))

    def test_progress_snapshots_contain_only_unfinished_items(self) -> None:
        """进度回调只携带尚未成功的输入项。"""
        work = [
            item(str(pmid), f"PMC{pmid}", {"pdf": f"https://a/{pmid}.pdf"}) for pmid in range(1, 16)
        ]
        snapshots: list[tuple[str, ...]] = []

        def download(current: PdfWorkItem) -> bool:
            """按 PMID 模拟一组成功和一组成败，以检查剩余集合。"""
            return int(current.pmid) <= 10

        result = execute_pdf_work_items(
            work,
            automatic_retry_rounds=0,
            local_fulltext_exists=lambda _current: False,
            download=download,
            progress=lambda remaining: snapshots.append(
                tuple(current.pmid for current in remaining)
            ),
        )

        self.assertEqual(snapshots, [tuple(str(i) for i in range(11, 16))])
        self.assertEqual(
            [current.pmid for current in result.failed], [str(i) for i in range(11, 16)]
        )


class TestPdfWorkCheckpoint(unittest.TestCase):
    """checkpoint 身份模型的持久化边界。"""

    def test_round_trip_persists_only_work_item_identity(self) -> None:
        """往返读写只保留工作项身份字段。"""
        items = [
            item("111", "PMC1", {"pdf": "https://a/1.pdf"}),
            item("222", "PMC2"),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "progress.json"

            save_pdf_work_checkpoint(path, items)
            loaded = load_pdf_work_checkpoint(path)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(loaded, tuple(items))
        self.assertEqual(
            payload["pending"],
            [
                {"pmid": "111", "pmc_id": "PMC1", "links": {"pdf": "https://a/1.pdf"}},
                {"pmid": "222", "pmc_id": "PMC2", "links": {}},
            ],
        )

    def test_invalid_outer_shape_or_json_returns_none(self) -> None:
        """损坏 JSON 或错误外层 shape 表示没有可恢复 checkpoint。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            invalid_json = root / "invalid.json"
            invalid_json.write_text("{not json", encoding="utf-8")
            wrong_shape = root / "wrong.json"
            wrong_shape.write_text(json.dumps({"pending": {"pmid": "111"}}), encoding="utf-8")

            self.assertIsNone(load_pdf_work_checkpoint(root / "missing.json"))
            self.assertIsNone(load_pdf_work_checkpoint(invalid_json))
            self.assertIsNone(load_pdf_work_checkpoint(wrong_shape))

    def test_invalid_items_are_skipped_without_losing_valid_items(self) -> None:
        """坏单项被隔离，合法单项仍可恢复。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "progress.json"
            path.write_text(
                json.dumps(
                    {
                        "pending": [
                            {"pmid": "111", "pmc_id": "PMC1", "links": {}},
                            "bad",
                            {"pmid": "", "pmc_id": "PMC2", "links": {}},
                            {"pmid": "444", "pmc_id": "PMC4", "links": {"pdf": 4}},
                            {
                                "pmid": "555",
                                "pmc_id": "PMC5",
                                "links": {"txt": "https://a/5.txt"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_pdf_work_checkpoint(path)

        self.assertEqual(
            loaded,
            (
                item("111", "PMC1"),
                item("555", "PMC5", {"txt": "https://a/5.txt"}),
            ),
        )


if __name__ == "__main__":
    unittest.main()
