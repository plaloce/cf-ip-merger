from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from .config import Source
from .downloader import DownloadClient, DownloadOutcome
from .merger import MergeError, merge_outcomes, write_merged_file

ProgressKind = Literal["started", "finished", "writing"]


@dataclass(slots=True)
class ProgressEvent:
    kind: ProgressKind
    index: int
    source: Source
    outcome: DownloadOutcome | None = None


@dataclass(slots=True)
class RunReport:
    total_sources: int
    active_sources: int
    outcomes: list[DownloadOutcome] = field(default_factory=list)
    success_count: int = 0
    failed_count: int = 0
    warning_count: int = 0
    total_lines: int = 0
    duplicates_removed: int = 0
    filtered_out: int = 0
    non_ip_filtered: int = 0
    source_line_counts: list[int] = field(default_factory=list)
    allowed_versions: frozenset[int] | None = None
    output_path: Path | None = None
    written: bool = False
    cancelled: bool = False
    fatal_error: str | None = None
    duration: float = 0.0

    @property
    def failures(self) -> list[DownloadOutcome]:
        return [item for item in self.outcomes if not item.success]

    @property
    def warnings(self) -> list[DownloadOutcome]:
        return [item for item in self.outcomes if item.warning]

    @property
    def partial(self) -> bool:
        return self.written and self.failed_count > 0


class MergeService:
    """控制有限并发下载，并始终按来源所在顺序合并。"""

    def __init__(
        self,
        downloader: DownloadClient | None = None,
        max_workers: int = 4,
    ) -> None:
        self.downloader = downloader or DownloadClient()
        self.max_workers = max(1, max_workers)

    def run(
        self,
        sources: list[Source],
        output_path: Path,
        deduplicate: bool = False,
        allowed_versions: set[int] | frozenset[int] | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> RunReport:
        started = time.monotonic()
        active = [(index, source) for index, source in enumerate(sources) if source.enabled]
        normalized_versions = (
            frozenset(allowed_versions) if allowed_versions is not None else None
        )

        def is_cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        def emit(event: ProgressEvent) -> None:
            if on_progress is None:
                return
            try:
                on_progress(event)
            except Exception:
                # 界面通知失败不应中断下载。
                pass

        report = RunReport(
            total_sources=len(sources),
            active_sources=len(active),
            allowed_versions=normalized_versions,
            output_path=output_path,
        )
        if not active:
            report.fatal_error = "没有已启用的来源，无法执行合并。"
            report.duration = time.monotonic() - started
            return report
        if is_cancelled():
            report.cancelled = True
            report.duration = time.monotonic() - started
            return report

        outcomes_by_index: dict[int, DownloadOutcome] = {}
        workers = min(self.max_workers, len(active))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        future_map = {
            executor.submit(self._download_one, index, source, emit): (index, source)
            for index, source in active
        }
        pending = set(future_map)
        cancelled = False
        try:
            while pending:
                if is_cancelled():
                    cancelled = True
                    break
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.12,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    index, source = future_map[future]
                    try:
                        outcome = future.result()
                    except Exception as exc:  # 防御未知线程异常
                        outcome = DownloadOutcome(
                            source=source,
                            error=f"未预期的下载错误：{exc}",
                        )
                    outcomes_by_index[index] = outcome
                    emit(
                        ProgressEvent(
                            kind="finished",
                            index=index,
                            source=source,
                            outcome=outcome,
                        )
                    )
        finally:
            if cancelled or is_cancelled():
                cancelled = True
                for future in pending:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

        ordered_outcomes = [
            outcomes_by_index[index] for index, _ in active if index in outcomes_by_index
        ]
        report.outcomes = ordered_outcomes
        report.success_count = sum(1 for item in ordered_outcomes if item.success)
        report.failed_count = sum(1 for item in ordered_outcomes if not item.success)
        report.warning_count = sum(
            1 for item in ordered_outcomes if item.success and item.warning
        )
        if cancelled or is_cancelled():
            report.cancelled = True
            report.total_lines = 0
            report.duration = time.monotonic() - started
            return report

        if report.success_count == 0:
            report.fatal_error = "所有启用的来源均下载失败，未生成或覆盖结果文件。"
            report.duration = time.monotonic() - started
            return report

        merged = merge_outcomes(
            ordered_outcomes,
            deduplicate=deduplicate,
            allowed_versions=normalized_versions,
        )
        report.total_lines = len(merged.lines)
        report.duplicates_removed = merged.duplicates_removed
        report.filtered_out = merged.filtered_out
        report.non_ip_filtered = merged.non_ip_filtered
        report.source_line_counts = merged.source_line_counts
        if not merged.lines:
            if normalized_versions is not None and report.filtered_out:
                report.fatal_error = (
                    "按所选 IP 类型筛选后没有可写入内容，已保留原有结果文件。"
                )
            else:
                report.fatal_error = (
                    "成功下载的来源均没有可合并内容，已保留原有结果文件。"
                )
            report.duration = time.monotonic() - started
            return report

        if is_cancelled():
            report.cancelled = True
            report.total_lines = 0
            report.duration = time.monotonic() - started
            return report

        first_index, first_source = active[0]
        emit(
            ProgressEvent(
                kind="writing",
                index=first_index,
                source=first_source,
            )
        )
        try:
            write_merged_file(output_path, merged.lines)
            report.written = True
        except MergeError as exc:
            report.fatal_error = str(exc)
        except OSError as exc:
            report.fatal_error = f"无法生成结果文件：{exc}"

        report.duration = time.monotonic() - started
        return report

    def _download_one(
        self,
        index: int,
        source: Source,
        emit: Callable[[ProgressEvent], None],
    ) -> DownloadOutcome:
        emit(ProgressEvent(kind="started", index=index, source=source))
        try:
            return self.downloader.download(source)
        except Exception as exc:
            return DownloadOutcome(
                source=source,
                error=f"下载过程发生错误：{exc}",
            )
