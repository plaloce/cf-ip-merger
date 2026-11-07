from __future__ import annotations

import codecs
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_HEADER = """\
# CF 优选 IP 合并工具 - URL 配置
# 每行一个来源，格式：URL，备注名称（英文逗号也可）
# 以 # 开头的普通行是注释；在 URL 前加 # 表示该来源已禁用
# 请在程序内保存，或编辑本文件后点击“重新加载”
"""


class ConfigError(ValueError):
    """配置无法读取或保存。"""


@dataclass(slots=True)
class Source:
    url: str
    name: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        self.url = self.url.strip()
        self.name = self.name.strip()

    def display_name(self, position: int | None = None) -> str:
        if self.name:
            return self.name
        if position is None:
            return "未命名来源"
        return f"来源 {position}"


@dataclass(slots=True)
class ConfigIssue:
    line_number: int
    message: str
    line_text: str = ""


@dataclass(slots=True)
class ConfigLoadResult:
    path: Path
    sources: list[Source] = field(default_factory=list)
    errors: list[ConfigIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exists: bool = True
    encoding: str = "utf-8"


def validate_url(url: str) -> str | None:
    """校验 URL，合法时返回 None，否则返回可展示的错误原因。"""
    value = url.strip()
    if not value:
        return "URL 不能为空"
    if any(char.isspace() for char in value):
        return "URL 中不能包含空格或换行"
    if any(ord(char) < 32 for char in value):
        return "URL 中不能包含控制字符"
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        return f"URL 格式错误：{exc}"
    if parsed.scheme.lower() not in {"http", "https"}:
        return "仅支持 http:// 或 https:// 开头的地址"
    if not parsed.netloc or not parsed.hostname:
        return "URL 缺少有效的主机名"
    host = parsed.hostname
    if (
        not any(char.isalnum() for char in host)
        or host.startswith(".")
        or host.endswith(".")
    ):
        return "URL 的主机名格式不正确"
    return None


def split_source_line(line: str) -> tuple[str, str]:
    """按第一个中文或英文逗号，拆出 URL 和备注名称。"""
    positions = [pos for pos in (line.find("，"), line.find(",")) if pos >= 0]
    if not positions:
        return line.strip(), ""
    pos = min(positions)
    return line[:pos].strip(), line[pos + 1 :].strip()


def _is_url(value: str) -> bool:
    lowered = value.lstrip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _duplicate_key(url: str) -> tuple[str, str, str, str, str]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        parsed.path,
        parsed.query,
        parsed.fragment,
    )


def _decode_config(raw: bytes) -> tuple[str, str]:
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030"), "gb18030"
    except UnicodeDecodeError as exc:
        raise ConfigError("文件编码无法识别，请将 URL.txt 另存为 UTF-8 后重试。") from exc


def load_config(path: Path) -> ConfigLoadResult:
    """读取配置文件，跳过无效行并记录具体错误。"""
    result = ConfigLoadResult(path=path)
    if not path.exists():
        result.exists = False
        return result
    if not path.is_file():
        raise ConfigError(f"配置路径不是文件：{path}")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{exc}") from exc

    try:
        text, encoding = _decode_config(raw)
    except ConfigError as exc:
        result.errors.append(ConfigIssue(0, str(exc)))
        return result
    result.encoding = encoding

    seen: dict[tuple[str, str, str, str, str], int] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue

        enabled = True
        candidate = stripped
        if stripped.startswith("#"):
            after_hash = stripped[1:].strip()
            # # 后紧跟 URL 时表示禁用来源；其余内容视为普通注释。
            if not _is_url(after_hash):
                continue
            candidate = after_hash
            enabled = False

        url, name = split_source_line(candidate)
        problem = validate_url(url)
        if problem:
            result.errors.append(
                ConfigIssue(line_number, problem, stripped[:300])
            )
            continue

        key = _duplicate_key(url)
        if key in seen:
            result.warnings.append(
                f"第 {line_number} 行与第 {seen[key]} 行的 URL 重复，已保留首次配置。"
            )
            continue
        seen[key] = line_number
        result.sources.append(Source(url=url, name=name, enabled=enabled))

    return result


def _sanitize_single_line(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()


def serialize_config(sources: list[Source]) -> str:
    lines = [CONFIG_HEADER.rstrip("\n")]
    seen: set[tuple[str, str, str, str, str]] = set()
    for index, source in enumerate(sources, start=1):
        url = _sanitize_single_line(source.url)
        name = _sanitize_single_line(source.name)
        problem = validate_url(url)
        if problem:
            raise ConfigError(f"第 {index} 条来源无效：{problem}")
        key = _duplicate_key(url)
        if key in seen:
            raise ConfigError(f"第 {index} 条来源与前面的 URL 重复：{url}")
        seen.add(key)
        prefix = "" if source.enabled else "# "
        line = f"{prefix}{url}"
        if name:
            line += f"，{name}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def save_config(path: Path, sources: list[Source]) -> None:
    """先写临时文件再原子替换，避免中途失败损坏原配置。"""
    payload = serialize_config(sources)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"无法创建配置目录：{exc}") from exc

    temp_path = path.parent / f".URL.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temp_path.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temp_path, path)
    except OSError as exc:
        raise ConfigError(f"无法保存配置文件：{exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
