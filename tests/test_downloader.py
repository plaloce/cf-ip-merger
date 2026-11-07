from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cf_merger.config import Source
from cf_merger.downloader import (
    DownloadClient,
    accelerate_github_url,
    is_github_url,
)


class _TestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ok":
            self._send(200, b"\xef\xbb\xbf1.1.1.1:443#A\r\n2.2.2.2:8443#B\r\n", "text/plain; charset=utf-8")
        elif self.path == "/gbk":
            self._send(200, "3.3.3.3:443#中文来源\n".encode("gb18030"), "text/plain")
        elif self.path == "/empty":
            self._send(200, b"\r\n  \r\n", "text/plain")
        elif self.path == "/html":
            self._send(200, b"<!DOCTYPE html><html><body>error</body></html>", "text/html")
        elif self.path == "/notfound":
            self._send(404, b"not found", "text/plain")
        elif self.path == "/error":
            self._send(500, b"server error", "text/plain")
        elif self.path == "/flaky":
            self.server.flaky_count += 1  # type: ignore[attr-defined]
            if self.server.flaky_count < 3:  # type: ignore[attr-defined]
                self._send(503, b"retry", "text/plain")
            else:
                self._send(200, b"4.4.4.4:443#OK\n", "text/plain")
        elif self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "999999")
            self.end_headers()
        elif self.path == "/binary":
            self._send(200, b"\x00\x01\x02\x03", "application/octet-stream")
        else:
            self._send(404, b"missing", "text/plain")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class DownloaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
        cls.server.daemon_threads = True
        cls.server.flaky_count = 0  # type: ignore[attr-defined]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def client(self, retries: int = 2) -> DownloadClient:
        return DownloadClient(timeout=3, retries=retries, retry_delay=0, max_bytes=4096)

    def test_utf8_bom_download_and_blank_lines(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/ok"))
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.lines, ["1.1.1.1:443#A", "2.2.2.2:8443#B"])
        self.assertEqual(outcome.http_status, 200)
        self.assertEqual(outcome.attempts, 1)

    def test_gb18030_fallback(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/gbk"))
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.lines, ["3.3.3.3:443#中文来源"])

    def test_empty_content_is_warning_not_total_failure(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/empty"))
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.lines, [])
        self.assertIn("空", outcome.warning or "")

    def test_html_is_rejected(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/html"))
        self.assertFalse(outcome.success)
        self.assertIn("HTML", outcome.error or "")

    def test_404_is_not_retried(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/notfound"))
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 1)
        self.assertIn("404", outcome.error or "")

    def test_500_is_retried_three_times(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/error"))
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.attempts, 3)

    def test_transient_503_then_success(self) -> None:
        self.server.flaky_count = 0  # type: ignore[attr-defined]
        outcome = self.client().download(Source(f"{self.base}/flaky"))
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(outcome.lines, ["4.4.4.4:443#OK"])

    def test_declared_size_limit(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/big"))
        self.assertFalse(outcome.success)
        self.assertIn("上限", outcome.error or "")

    def test_binary_content_rejected(self) -> None:
        outcome = self.client().download(Source(f"{self.base}/binary"))
        self.assertFalse(outcome.success)
        self.assertIn("二进制", outcome.error or "")

    def test_cancel_before_download_stops_immediately(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()
        client = DownloadClient(
            timeout=2,
            retries=0,
            retry_delay=0,
            cancel_event=cancel_event,
        )
        outcome = client.download(Source(f"{self.base}/ok"))
        self.assertFalse(outcome.success)
        self.assertIn("停止", outcome.error or "")
        self.assertEqual(outcome.byte_count, 0)


class GithubAccelerationTests(unittest.TestCase):
    RAW_URL = (
        "https://raw.githubusercontent.com/owner/repo/"
        "refs/heads/main/best_ips.txt"
    )

    def test_recognizes_github_hosts(self) -> None:
        self.assertTrue(is_github_url(self.RAW_URL))
        self.assertTrue(is_github_url("https://github.com/user/repo/file.txt"))
        self.assertTrue(is_github_url("https://gist.githubusercontent.com/a/b"))
        self.assertFalse(is_github_url("https://github.com.evil.example/a.txt"))
        self.assertFalse(is_github_url("https://notgithub.com/a.txt"))
        self.assertFalse(is_github_url("https://example.com/github/file.txt"))

    def test_proxy_url_matches_required_format(self) -> None:
        expected = f"https://gh-proxy.org/{self.RAW_URL}"
        self.assertEqual(accelerate_github_url(self.RAW_URL), expected)

    def test_non_github_and_already_proxied_urls_are_unchanged(self) -> None:
        normal = "https://bestcf.pages.dev/domain/all.txt"
        proxied = f"https://gh-proxy.org/{self.RAW_URL}"
        self.assertEqual(accelerate_github_url(normal), normal)
        self.assertEqual(accelerate_github_url(proxied), proxied)

    def test_download_client_uses_proxy_only_when_enabled(self) -> None:
        client = DownloadClient(
            timeout=2,
            retries=0,
            retry_delay=0,
            github_accelerate=True,
        )
        with patch.object(
            client,
            "_fetch",
            return_value=(b"1.1.1.1:443\n", 200, 12),
        ) as fetch:
            outcome = client.download(Source(self.RAW_URL))
        fetch.assert_called_once_with(f"https://gh-proxy.org/{self.RAW_URL}")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.download_url, f"https://gh-proxy.org/{self.RAW_URL}")
        self.assertEqual(outcome.source.url, self.RAW_URL)

    def test_download_client_does_not_proxy_when_disabled(self) -> None:
        client = DownloadClient(timeout=2, retries=0, retry_delay=0)
        with patch.object(
            client,
            "_fetch",
            return_value=(b"1.1.1.1:443\n", 200, 12),
        ) as fetch:
            outcome = client.download(Source(self.RAW_URL))
        fetch.assert_called_once_with(self.RAW_URL)
        self.assertEqual(outcome.download_url, self.RAW_URL)


if __name__ == "__main__":
    unittest.main()
