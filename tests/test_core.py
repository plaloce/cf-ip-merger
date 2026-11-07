from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cf_merger.config import (
    ConfigError,
    Source,
    load_config,
    save_config,
    split_source_line,
    validate_url,
)
from cf_merger.merger import MergeError, merge_outcomes, write_merged_file
from cf_merger.downloader import DownloadOutcome


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "URL.txt"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parse_fullwidth_and_english_commas(self) -> None:
        self.path.write_text(
            "\ufeff# 注释\n"
            "https://example.com/a.txt，中文备注\n"
            "https://example.com/b.txt, English name\n"
            "https://example.com/c.txt\n"
            "# https://example.com/d.txt，停用来源\n"
            "\n",
            encoding="utf-8",
        )
        result = load_config(self.path)
        self.assertEqual(len(result.sources), 4)
        self.assertEqual(result.sources[0].name, "中文备注")
        self.assertEqual(result.sources[1].name, "English name")
        self.assertEqual(result.sources[2].name, "")
        self.assertFalse(result.sources[3].enabled)
        self.assertEqual(result.errors, [])

    def test_invalid_and_duplicate_lines_are_skipped(self) -> None:
        self.path.write_text(
            "https://example.com/a.txt，A\n"
            "ftp://example.com/f.txt，FTP\n"
            "not a url\n"
            "https://example.com/a.txt，重复\n"
            "https://example.com/b.txt，B\n",
            encoding="utf-8",
        )
        result = load_config(self.path)
        self.assertEqual([item.name for item in result.sources], ["A", "B"])
        self.assertEqual(len(result.errors), 2)
        self.assertEqual(result.errors[0].line_number, 2)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("第 4 行", result.warnings[0])

    def test_missing_config_returns_empty(self) -> None:
        result = load_config(self.path)
        self.assertFalse(result.exists)
        self.assertEqual(result.sources, [])

    def test_save_and_reload_roundtrip(self) -> None:
        sources = [
            Source("https://example.com/a.txt", "名称，含逗号", True),
            Source("https://example.com/b.txt", "停用", False),
        ]
        save_config(self.path, sources)
        result = load_config(self.path)
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(result.sources[0].name, "名称，含逗号")
        self.assertTrue(result.sources[0].enabled)
        self.assertEqual(result.sources[1].name, "停用")
        self.assertFalse(result.sources[1].enabled)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("# https://example.com/b.txt，停用", saved)

    def test_save_rejects_invalid_url_and_preserves_existing(self) -> None:
        self.path.write_text("https://example.com/a.txt，A\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            save_config(self.path, [Source("bad-url", "错误")])
        result = load_config(self.path)
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].name, "A")

    def test_utf16_config_is_readable(self) -> None:
        self.path.write_bytes(
            "https://example.com/a.txt，Windows 记事本\n".encode("utf-16")
        )
        result = load_config(self.path)
        self.assertEqual(result.encoding, "utf-16")
        self.assertEqual(result.sources[0].name, "Windows 记事本")

    def test_helpers(self) -> None:
        self.assertEqual(
            split_source_line("https://example.com/a.txt，备注,后段"),
            ("https://example.com/a.txt", "备注,后段"),
        )
        self.assertIsNone(validate_url("https://example.com/a.txt"))
        self.assertIsNotNone(validate_url("https://example.com/a b.txt"))
        self.assertIsNotNone(validate_url("javascript:alert(1)"))


class MergerTests(unittest.TestCase):
    def _make_outcome(self, url: str, lines: list[str], error: str | None = None):
        return DownloadOutcome(
            source=Source(url),
            lines=lines,
            error=error,
        )

    def test_order_and_exact_deduplication(self) -> None:
        outcomes = [
            self._make_outcome("https://a.test", ["1.1.1.1", "2.2.2.2"]),
            self._make_outcome("https://b.test", ["2.2.2.2", "3.3.3.3"]),
        ]
        merged = merge_outcomes(outcomes, deduplicate=False)
        self.assertEqual(merged.lines, ["1.1.1.1", "2.2.2.2", "2.2.2.2", "3.3.3.3"])
        deduped = merge_outcomes(outcomes, deduplicate=True)
        self.assertEqual(deduped.lines, ["1.1.1.1", "2.2.2.2", "3.3.3.3"])
        self.assertEqual(deduped.duplicates_removed, 1)

    def test_failed_source_contributes_no_lines(self) -> None:
        outcomes = [
            self._make_outcome("https://a.test", [], error="HTTP 500"),
            self._make_outcome("https://b.test", ["3.3.3.3"]),
        ]
        merged = merge_outcomes(outcomes)
        self.assertEqual(merged.lines, ["3.3.3.3"])
        self.assertEqual(merged.source_line_counts, [0, 1])

    def test_ip_version_filter_and_deduplication(self) -> None:
        outcomes = [
            self._make_outcome(
                "https://a.test",
                [
                    "1.1.1.1:443#A",
                    "2606:4700::1:443#V6",
                    "na.example.com:443#domain",
                    "1.1.1.1:443#A",
                ],
            ),
            self._make_outcome(
                "https://b.test",
                ["9.9.9.9:8443#B", "[2606:4700::2]:443#V6-2"],
            ),
        ]
        merged = merge_outcomes(
            outcomes,
            deduplicate=True,
            allowed_versions={4},
        )
        self.assertEqual(merged.lines, ["1.1.1.1:443#A", "9.9.9.9:8443#B"])
        self.assertEqual(merged.duplicates_removed, 1)
        self.assertEqual(merged.filtered_out, 3)
        self.assertEqual(merged.non_ip_filtered, 1)
        self.assertEqual(merged.source_line_counts, [2, 1])

        both = merge_outcomes(
            outcomes,
            deduplicate=True,
            allowed_versions={4, 6},
        )
        self.assertEqual(len(both.lines), 4)
        self.assertEqual(both.duplicates_removed, 1)
        self.assertEqual(both.non_ip_filtered, 1)

    def test_empty_ip_selection_filters_everything(self) -> None:
        merged = merge_outcomes(
            [self._make_outcome("https://a.test", ["1.1.1.1", "2606::1"])],
            allowed_versions=set(),
        )
        self.assertEqual(merged.lines, [])
        self.assertEqual(merged.filtered_out, 2)

    def test_write_utf8_crlf_without_bom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "优选合集.txt"
            size = write_merged_file(path, ["1.1.1.1:443#A", "2.2.2.2:8443#B"])
            raw = path.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(raw, "1.1.1.1:443#A\r\n2.2.2.2:8443#B\r\n".encode("utf-8"))
            self.assertEqual(size, len(raw))

    def test_empty_write_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "优选合集.txt"
            path.write_text("原内容\n", encoding="utf-8")
            with self.assertRaises(MergeError):
                write_merged_file(path, [])
            self.assertEqual(path.read_text(encoding="utf-8"), "原内容\n")


if __name__ == "__main__":
    unittest.main()
