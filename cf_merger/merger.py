from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .downloader import DownloadOutcome
from .ip_filter import filter_ip_lines


class MergeError(RuntimeError):
    """合并或写入结果失败。"""


@dataclass(slots=True)
class MergedContent:
    lines: list[str]
    duplicates_removed: int
    source_line_counts: list[int]
    filtered_out: int = 0
    non_ip_filtered: int = 0


def merge_outcomes(
    outcomes: list[DownloadOutcome],
    deduplicate: bool = False,
    allowed_versions: set[int] | frozenset[int] | None = None,
) -> MergedContent:
    """按传入顺序合并成功来源，保持原始行内容。

    allowed_versions 为 None 时不做 IP 类型筛选（供内部测试与扩展使用）；
    界面始终显式传入 IPv4/IPv6 选择。
    """
    combined: list[str] = []
    counts: list[int] = []
    seen: set[str] = set()
    duplicates_removed = 0
    filtered_out = 0
    non_ip_filtered = 0

    for outcome in outcomes:
        if not outcome.success:
            counts.append(0)
            continue
        source_lines = outcome.lines
        if allowed_versions is not None:
            filtered = filter_ip_lines(outcome.lines, allowed_versions)
            source_lines = filtered.lines
            filtered_out += filtered.excluded
            non_ip_filtered += filtered.non_ip_excluded
        counts.append(len(source_lines))
        for line in source_lines:
            if deduplicate:
                if line in seen:
                    duplicates_removed += 1
                    continue
                seen.add(line)
            combined.append(line)

    return MergedContent(
        lines=combined,
        duplicates_removed=duplicates_removed,
        source_line_counts=counts,
        filtered_out=filtered_out,
        non_ip_filtered=non_ip_filtered,
    )


def write_merged_file(path: Path, lines: list[str]) -> int:
    """以 UTF-8 无 BOM、CRLF 换行原子写入结果文件。"""
    if not lines:
        raise MergeError("没有可写入的内容，已保留原有结果文件。")

    data = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(data)
        os.replace(temp_path, path)
    except OSError as exc:
        raise MergeError(f"无法写入结果文件：{exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return len(data)
