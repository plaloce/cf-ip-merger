from __future__ import annotations

import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from . import __version__
from .config import Source

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2
RETRYABLE_STATUS = {408, 425, 429}
GITHUB_PROXY_PREFIX = "https://gh-proxy.org/"


def is_github_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").casefold()
    return (
        host == "github.com"
        or host.endswith(".github.com")
        or host.endswith(".githubusercontent.com")
    )


def accelerate_github_url(url: str) -> str:
    """给 GitHub 地址加上 gh-proxy 前缀；非 GitHub 或已代理地址保持原样。"""
    value = url.strip()
    if not value or value.casefold().startswith(GITHUB_PROXY_PREFIX.casefold()):
        return url
    if not is_github_url(value):
        return url
    return GITHUB_PROXY_PREFIX + value


class _RetryableError(RuntimeError):
    pass


class _ContentError(RuntimeError):
    pass


class _CancelledError(RuntimeError):
    pass


@dataclass(slots=True)
class DownloadOutcome:
    source: Source
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    warning: str | None = None
    attempts: int = 0
    byte_count: int = 0
    duration: float = 0.0
    http_status: int | None = None
    download_url: str = ""

    @property
    def success(self) -> bool:
        return self.error is None


class DownloadClient:
    """下载来源文本，带有限重试和内容安全检查。"""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        retry_delay: float = 0.45,
        sleep: Callable[[float], None] = time.sleep,
        github_accelerate: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, retries)
        self.max_bytes = max_bytes
        self.retry_delay = max(0.0, retry_delay)
        self._sleep = sleep
        self.github_accelerate = github_accelerate
        self.cancel_event = cancel_event
        self.user_agent = (
            f"CFBestIPMerger/{__version__} "
            "(Windows; +https://bestcf.pages.dev)"
        )

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise _CancelledError("合并已停止")

    def download(self, source: Source) -> DownloadOutcome:
        started = time.monotonic()
        attempts = 0
        last_error = "未知下载错误"
        download_url = (
            accelerate_github_url(source.url)
            if self.github_accelerate
            else source.url
        )

        for attempt in range(1, self.retries + 2):
            attempts = attempt
            try:
                self._check_cancelled()
                content, status, byte_count = self._fetch(download_url)
                self._check_cancelled()
                text = self._decode(content)
                lines = self._extract_lines(text)
                warning = None
                if not lines:
                    warning = "内容为空，或只有空白行"
                return DownloadOutcome(
                    source=source,
                    lines=lines,
                    warning=warning,
                    attempts=attempts,
                    byte_count=byte_count,
                    duration=time.monotonic() - started,
                    http_status=status,
                    download_url=download_url,
                )
            except _CancelledError:
                last_error = "合并已停止"
                break
            except _ContentError as exc:
                last_error = str(exc)
                break
            except _RetryableError as exc:
                last_error = str(exc)
                if attempt <= self.retries:
                    self._sleep(self.retry_delay * attempt)
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                last_error = f"HTTP {status} {exc.reason}"
                exc.close()
                retryable = status in RETRYABLE_STATUS or status >= 500
                if not retryable:
                    break
                if attempt <= self.retries:
                    self._sleep(self.retry_delay * attempt)
            except urllib.error.URLError as exc:
                reason = exc.reason
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    last_error = "连接超时"
                elif isinstance(reason, ssl.SSLError):
                    last_error = f"TLS 证书或连接失败：{reason}"
                else:
                    last_error = f"网络错误：{reason}"
                if isinstance(reason, ssl.SSLError):
                    break
                if attempt <= self.retries:
                    self._sleep(self.retry_delay * attempt)
            except (TimeoutError, socket.timeout):
                last_error = "连接超时"
                if attempt <= self.retries:
                    self._sleep(self.retry_delay * attempt)
            except ssl.SSLError as exc:
                last_error = f"TLS 连接失败：{exc}"
                break
            except OSError as exc:
                last_error = f"网络连接失败：{exc}"
                if attempt <= self.retries:
                    self._sleep(self.retry_delay * attempt)

        return DownloadOutcome(
            source=source,
            error=last_error,
            attempts=attempts,
            duration=time.monotonic() - started,
            download_url=download_url,
        )

    def _fetch(self, url: str) -> tuple[bytes, int, int]:
        self._check_cancelled()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/plain, */*;q=0.8",
            },
            method="GET",
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError:
            raise
        except (TimeoutError, socket.timeout):
            raise
        except ssl.SSLError:
            raise

        with response:
            self._check_cancelled()
            status = int(getattr(response, "status", response.getcode()))
            header = response.headers.get("Content-Length")
            if header:
                try:
                    declared_size = int(header)
                except ValueError:
                    declared_size = 0
                if declared_size > self.max_bytes:
                    raise _ContentError(
                        f"文件超过 {self.max_bytes // 1024 // 1024} MB 上限，已停止下载"
                    )
            chunks: list[bytes] = []
            total = 0
            read_chunk = getattr(response, "read1", response.read)
            while True:
                self._check_cancelled()
                chunk = read_chunk(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > self.max_bytes:
                    raise _ContentError(
                        f"文件超过 {self.max_bytes // 1024 // 1024} MB 上限，已停止下载"
                    )
            content = b"".join(chunks)
        if not 200 <= status < 300:
            if status in RETRYABLE_STATUS or status >= 500:
                raise _RetryableError(f"HTTP {status}")
            raise _ContentError(f"HTTP {status}")
        if not content:
            return b"", status, 0
        return content, status, len(content)

    @staticmethod
    def _decode(content: bytes) -> str:
        if b"\x00" in content:
            raise _ContentError("来源内容疑似二进制文件，不是有效 TXT")
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
        try:
            return content.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise _ContentError("文本编码无法识别，无法合并") from exc

    @staticmethod
    def _extract_lines(text: str) -> list[str]:
        without_bom = text.lstrip("\ufeff")
        head = without_bom.lstrip()[:512].casefold()
        if (
            head.startswith("<!doctype html")
            or head.startswith("<html")
            or head.startswith("<head")
        ):
            raise _ContentError("来源返回了 HTML 页面，不是有效 TXT 内容")
        return [line for line in without_bom.splitlines() if line.strip()]
