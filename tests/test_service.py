from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from cf_merger.config import Source
from cf_merger.downloader import DownloadOutcome
from cf_merger.service import MergeService, ProgressEvent


class _FakeDownloader:
    def __init__(self, mapping: dict[str, tuple[list[str], str | None, float]]) -> None:
        self.mapping = mapping

    def download(self, source: Source) -> DownloadOutcome:
        lines, error, delay = self.mapping[source.url]
        time.sleep(delay)
        return DownloadOutcome(source=source, lines=lines, error=error)


class ServiceTests(unittest.TestCase):
    def test_merge_preserves_source_order_despite_completion_order(self) -> None:
        sources = [
            Source("https://a.test/a.txt", "A"),
            Source("https://b.test/b.txt", "B"),
            Source("https://c.test/c.txt", "C"),
        ]
        downloader = _FakeDownloader(
            {
                sources[0].url: (["A1", "A2"], None, 0.08),
                sources[1].url: (["B1"], None, 0.01),
                sources[2].url: (["C1"], None, 0.04),
            }
        )
        events: list[ProgressEvent] = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "优选合集.txt"
            report = MergeService(downloader, max_workers=3).run(
                sources, output, on_progress=events.append
            )
            self.assertTrue(report.written)
            self.assertEqual(report.success_count, 3)
            self.assertEqual(report.total_lines, 4)
            self.assertEqual(
                output.read_bytes(),
                b"A1\r\nA2\r\nB1\r\nC1\r\n",
            )
            self.assertEqual(
                [event.kind for event in events].count("started"), 3
            )
            self.assertEqual(
                [event.kind for event in events].count("finished"), 3
            )
            self.assertIn("writing", [event.kind for event in events])

    def test_disabled_source_is_not_downloaded(self) -> None:
        sources = [
            Source("https://a.test/a.txt", "A", True),
            Source("https://b.test/b.txt", "B", False),
        ]
        downloader = _FakeDownloader({sources[0].url: (["A"], None, 0)})
        with tempfile.TemporaryDirectory() as directory:
            report = MergeService(downloader).run(
                sources, Path(directory) / "out.txt"
            )
            self.assertEqual(report.active_sources, 1)
            self.assertEqual(report.total_sources, 2)
            self.assertEqual(report.success_count, 1)
            self.assertTrue(report.written)

    def test_partial_failure_writes_successful_sources(self) -> None:
        sources = [
            Source("https://a.test/a.txt", "A"),
            Source("https://b.test/b.txt", "B"),
        ]
        downloader = _FakeDownloader(
            {
                sources[0].url: (["A1"], None, 0.0),
                sources[1].url: ([], "HTTP 500", 0.0),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            report = MergeService(downloader).run(
                sources, Path(directory) / "out.txt"
            )
            self.assertTrue(report.written)
            self.assertTrue(report.partial)
            self.assertEqual(report.failed_count, 1)
            self.assertEqual(report.success_count, 1)

    def test_all_failed_does_not_overwrite_existing_output(self) -> None:
        sources = [Source("https://a.test/a.txt", "A")]
        downloader = _FakeDownloader(
            {sources[0].url: ([], "连接超时", 0.0)}
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "优选合集.txt"
            output.write_bytes("旧内容\r\n".encode("utf-8"))
            report = MergeService(downloader).run(sources, output)
            self.assertFalse(report.written)
            self.assertIsNotNone(report.fatal_error)
            self.assertEqual(output.read_bytes(), "旧内容\r\n".encode("utf-8"))

    def test_deduplication(self) -> None:
        sources = [
            Source("https://a.test/a.txt", "A"),
            Source("https://b.test/b.txt", "B"),
        ]
        downloader = _FakeDownloader(
            {
                sources[0].url: (["x", "y"], None, 0),
                sources[1].url: (["y", "z"], None, 0),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.txt"
            report = MergeService(downloader).run(
                sources, output, deduplicate=True
            )
            self.assertTrue(report.written)
            self.assertEqual(report.duplicates_removed, 1)
            self.assertEqual(output.read_bytes(), b"x\r\ny\r\nz\r\n")

    def test_ip_filter_reports_stats_and_writes_only_selected_version(self) -> None:
        sources = [
            Source("https://a.test/a.txt", "A"),
            Source("https://b.test/b.txt", "B"),
        ]
        downloader = _FakeDownloader(
            {
                sources[0].url: (
                    ["1.1.1.1:443#V4", "2606:4700::1#V6", "domain.example:443#D"],
                    None,
                    0,
                ),
                sources[1].url: (["1.1.1.1:443#V4", "9.9.9.9:8443#V4-2"], None, 0),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.txt"
            report = MergeService(downloader).run(
                sources,
                output,
                deduplicate=True,
                allowed_versions={4},
            )
            self.assertTrue(report.written)
            self.assertEqual(report.total_lines, 2)
            self.assertEqual(report.duplicates_removed, 1)
            self.assertEqual(report.filtered_out, 2)
            self.assertEqual(report.non_ip_filtered, 1)
            self.assertEqual(report.source_line_counts, [1, 2])
            self.assertEqual(
                output.read_bytes(),
                b"1.1.1.1:443#V4\r\n9.9.9.9:8443#V4-2\r\n",
            )

    def test_no_matching_ip_does_not_overwrite_existing_output(self) -> None:
        sources = [Source("https://a.test/a.txt", "A")]
        downloader = _FakeDownloader(
            {sources[0].url: (["2606:4700::1#V6", "域名字符串"], None, 0)}
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.txt"
            output.write_bytes(b"old\r\n")
            report = MergeService(downloader).run(
                sources,
                output,
                allowed_versions={4},
            )
            self.assertFalse(report.written)
            self.assertIn("筛选", report.fatal_error or "")
            self.assertEqual(report.filtered_out, 2)
            self.assertEqual(output.read_bytes(), b"old\r\n")

    def test_cancel_returns_quickly_without_writing_output(self) -> None:
        sources = [
            Source("https://a.test/a.txt", "A"),
            Source("https://b.test/b.txt", "B"),
        ]
        downloader = _FakeDownloader(
            {
                sources[0].url: (["1.1.1.1"], None, 0.05),
                sources[1].url: (["2.2.2.2"], None, 1.0),
            }
        )
        cancel_event = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.txt"
            output.write_bytes(b"old\r\n")
            timer = threading.Timer(0.18, cancel_event.set)
            timer.start()
            started = time.monotonic()
            report = MergeService(downloader, max_workers=2).run(
                sources,
                output,
                cancel_event=cancel_event,
            )
            elapsed = time.monotonic() - started
            timer.cancel()
            self.assertTrue(report.cancelled)
            self.assertFalse(report.written)
            self.assertLess(elapsed, 0.8)
            self.assertEqual(output.read_bytes(), b"old\r\n")

    def test_cancel_before_start_does_no_work(self) -> None:
        sources = [Source("https://a.test/a.txt", "A")]
        downloader = _FakeDownloader({sources[0].url: (["1.1.1.1"], None, 0.05)})
        cancel_event = threading.Event()
        cancel_event.set()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.txt"
            report = MergeService(downloader).run(
                sources,
                output,
                cancel_event=cancel_event,
            )
            self.assertTrue(report.cancelled)
            self.assertFalse(report.written)
            self.assertEqual(report.outcomes, [])
            self.assertFalse(output.exists())

    def test_no_active_sources_returns_fatal_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.txt"
            report = MergeService(_FakeDownloader({})).run(
                [Source("https://a.test/a.txt", "A", False)], output
            )
            self.assertFalse(report.written)
            self.assertEqual(report.active_sources, 0)
            self.assertFalse(output.exists())
            self.assertIn("没有已启用", report.fatal_error or "")


if __name__ == "__main__":
    unittest.main()
