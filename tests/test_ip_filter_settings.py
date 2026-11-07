from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cf_merger.ip_filter import (
    filter_ip_lines,
    ip_filter_label,
    ip_version_of_line,
)


class IpFilterTests(unittest.TestCase):
    def test_detects_common_ipv4_formats(self) -> None:
        cases = {
            "1.1.1.1": 4,
            "1.1.1.1:443": 4,
            "162.159.198.1:8443#官方入口": 4,
            "104.16.0.1:2053,速度信息": 4,
            "  198.41.0.1  ": 4,
        }
        for line, version in cases.items():
            with self.subTest(line=line):
                self.assertEqual(ip_version_of_line(line), version)

    def test_detects_common_ipv6_formats(self) -> None:
        cases = {
            "2606:4700:4700::1111": 6,
            "2606:4700:4700::1111#IPv6": 6,
            "[2606:4700:4700::1111]:443#IPv6": 6,
            "[2001:db8::1]:8443": 6,
            "::1": 6,
        }
        for line, version in cases.items():
            with self.subTest(line=line):
                self.assertEqual(ip_version_of_line(line), version)

    def test_non_ip_and_invalid_addresses_are_rejected(self) -> None:
        cases = [
            "",
            "# 这是注释",
            "https://bestcf.pages.dev/domain/all.txt",
            "na.877774.xyz:443#域名节点",
            "999.1.1.1:443",
            "[not-an-ip]:443",
            "<html>error</html>",
        ]
        for line in cases:
            with self.subTest(line=line):
                self.assertIsNone(ip_version_of_line(line))

    def test_filter_keeps_only_selected_version(self) -> None:
        lines = [
            "1.1.1.1:443#V4",
            "2606:4700::1#V6",
            "na.example.com:443#域名",
            "9.9.9.9:8443#V4-2",
        ]
        result = filter_ip_lines(lines, {4})
        self.assertEqual(result.lines, ["1.1.1.1:443#V4", "9.9.9.9:8443#V4-2"])
        self.assertEqual(result.excluded, 2)
        self.assertEqual(result.non_ip_excluded, 1)

        both = filter_ip_lines(lines, {4, 6})
        self.assertEqual(len(both.lines), 3)
        self.assertEqual(both.non_ip_excluded, 1)

        none = filter_ip_lines(lines, set())
        self.assertEqual(none.lines, [])
        self.assertEqual(none.excluded, 4)

    def test_labels(self) -> None:
        self.assertEqual(ip_filter_label({4}), "IPv4")
        self.assertEqual(ip_filter_label({6}), "IPv6")
        self.assertEqual(ip_filter_label({4, 6}), "IPv4 + IPv6")
        self.assertEqual(ip_filter_label(set()), "未选择")


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        from cf_merger.settings import UiSettings

        self.defaults = UiSettings()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "程序配置.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_defaults(self) -> None:
        self.assertTrue(self.defaults.deduplicate)
        self.assertTrue(self.defaults.ipv4)
        self.assertFalse(self.defaults.ipv6)
        self.assertFalse(self.defaults.github_accelerate)

    def test_missing_file_uses_defaults(self) -> None:
        from cf_merger.settings import load_settings

        result = load_settings(self.path)
        self.assertFalse(result.exists)
        self.assertIsNone(result.error)
        self.assertEqual(result.settings, self.defaults)

    def test_save_and_load_roundtrip(self) -> None:
        from cf_merger.settings import UiSettings, load_settings, save_settings

        settings = UiSettings(
            deduplicate=False,
            ipv4=False,
            ipv6=True,
            github_accelerate=True,
        )
        save_settings(self.path, settings)
        loaded = load_settings(self.path)
        self.assertTrue(loaded.exists)
        self.assertEqual(loaded.settings, settings)
        raw = self.path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))

    def test_partial_file_uses_defaults_for_missing_keys(self) -> None:
        from cf_merger.settings import load_settings

        self.path.write_text('{"deduplicate": false}', encoding="utf-8")
        result = load_settings(self.path)
        self.assertIsNone(result.error)
        self.assertFalse(result.settings.deduplicate)
        self.assertTrue(result.settings.ipv4)
        self.assertFalse(result.settings.ipv6)
        self.assertFalse(result.settings.github_accelerate)

    def test_invalid_json_falls_back_to_defaults(self) -> None:
        from cf_merger.settings import load_settings

        self.path.write_text("{broken", encoding="utf-8")
        result = load_settings(self.path)
        self.assertEqual(result.settings, self.defaults)
        self.assertIsNotNone(result.error)

    def test_wrong_types_fall_back_to_defaults(self) -> None:
        from cf_merger.settings import load_settings

        self.path.write_text(
            '{"deduplicate": "yes", "ipv4": true, "ipv6": false}',
            encoding="utf-8",
        )
        result = load_settings(self.path)
        self.assertEqual(result.settings, self.defaults)
        self.assertIn("deduplicate", result.error or "")


if __name__ == "__main__":
    unittest.main()
