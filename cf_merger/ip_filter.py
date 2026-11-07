from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

# IPv4、IPv6、端口、作用域标识可能出现的字符；遇到空格、#、逗号、竖线等即停止。
_ADDRESS_TOKEN = re.compile(r"^[0-9A-Fa-f:.%]+")
_BRACKET_ADDRESS = re.compile(r"^\[([^\]\s]+)\](?::\d+)?(?:$|[\s#,\|])")


@dataclass(slots=True)
class IpFilterResult:
    lines: list[str]
    excluded: int
    non_ip_excluded: int


def _version_of_address(value: str) -> int | None:
    try:
        return ipaddress.ip_address(value).version
    except ValueError:
        return None


def ip_version_of_line(line: str) -> int | None:
    """提取行首 IP 类型，兼容 IPv4:端口、[IPv6]:端口 等常见格式。

    只识别行首地址；域名、普通文本、HTML 片段和注释都不属于 IP 条目。
    """
    text = line.lstrip("\ufeff").strip()
    if not text or text.startswith("#"):
        return None

    if text.startswith("["):
        match = _BRACKET_ADDRESS.match(text)
        if not match:
            return None
        return _version_of_address(match.group(1))

    token_match = _ADDRESS_TOKEN.match(text)
    if not token_match:
        return None
    token = token_match.group(0)
    version = _version_of_address(token)
    if version is not None:
        return version

    # 常见 IPv4:端口格式；仅剥离一个数字端口，不修改原始输出行。
    if token.count(":") == 1:
        host, port = token.split(":", 1)
        if port.isdigit():
            return _version_of_address(host)
    return None


def filter_ip_lines(
    lines: list[str],
    versions: set[int] | frozenset[int],
) -> IpFilterResult:
    allowed = set(versions)
    selected: list[str] = []
    excluded = 0
    non_ip_excluded = 0

    for line in lines:
        version = ip_version_of_line(line)
        if version is not None and version in allowed:
            selected.append(line)
            continue
        excluded += 1
        if version is None:
            non_ip_excluded += 1

    return IpFilterResult(
        lines=selected,
        excluded=excluded,
        non_ip_excluded=non_ip_excluded,
    )


def ip_filter_label(versions: set[int] | frozenset[int]) -> str:
    labels: list[str] = []
    if 4 in versions:
        labels.append("IPv4")
    if 6 in versions:
        labels.append("IPv6")
    return " + ".join(labels) if labels else "未选择"
