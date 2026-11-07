from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


class SettingsError(ValueError):
    """程序参数无法读取或保存。"""


@dataclass(frozen=True, slots=True)
class UiSettings:
    deduplicate: bool = True
    ipv4: bool = True
    ipv6: bool = False
    github_accelerate: bool = False


@dataclass(frozen=True, slots=True)
class SettingsLoadResult:
    settings: UiSettings
    exists: bool
    error: str | None = None


def _parse_settings(data: object) -> UiSettings:
    if not isinstance(data, dict):
        raise SettingsError("程序参数文件的根内容必须是 JSON 对象。")

    defaults = UiSettings()
    values = {
        "deduplicate": defaults.deduplicate,
        "ipv4": defaults.ipv4,
        "ipv6": defaults.ipv6,
        "github_accelerate": defaults.github_accelerate,
    }
    for key in values:
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, bool):
            raise SettingsError(f"参数 {key} 必须是 true 或 false。")
        values[key] = value
    return UiSettings(**values)


def load_settings(path: Path) -> SettingsLoadResult:
    """读取界面参数；文件损坏或类型错误时回退到默认值。"""
    defaults = UiSettings()
    if not path.exists():
        return SettingsLoadResult(settings=defaults, exists=False)
    if not path.is_file():
        return SettingsLoadResult(
            settings=defaults,
            exists=False,
            error=f"参数路径不是文件：{path}",
        )

    try:
        raw = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return SettingsLoadResult(
            settings=defaults,
            exists=True,
            error=f"无法读取程序参数：{exc}",
        )

    try:
        data = json.loads(raw)
        settings = _parse_settings(data)
    except (json.JSONDecodeError, SettingsError) as exc:
        return SettingsLoadResult(
            settings=defaults,
            exists=True,
            error=f"程序参数格式无效，已使用默认值：{exc}",
        )
    return SettingsLoadResult(settings=settings, exists=True)


def save_settings(path: Path, settings: UiSettings) -> None:
    """自动保存界面参数，使用临时文件原子替换。"""
    payload = {
        "version": 2,
        "deduplicate": settings.deduplicate,
        "ipv4": settings.ipv4,
        "ipv6": settings.ipv6,
        "github_accelerate": settings.github_accelerate,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SettingsError(f"无法创建程序参数目录：{exc}") from exc

    temp_path = path.parent / f".settings.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temp_path, path)
    except OSError as exc:
        raise SettingsError(f"无法保存程序参数：{exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
