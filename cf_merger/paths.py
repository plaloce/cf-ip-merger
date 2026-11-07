from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_FILENAME = "URL.txt"
SETTINGS_FILENAME = "程序配置.json"
OUTPUT_FILENAME = "优选合集.txt"


def get_app_dir() -> Path:
    """返回配置与结果的读写目录。

    PyInstaller 单文件模式运行时，程序文件位于临时解压目录；用户数据必须始终
    放在 exe 自己所在目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_config_path(app_dir: Path | None = None) -> Path:
    """返回 URL 配置文件路径，兼容 URL.txt / url.txt 的大小写写法。"""
    directory = app_dir or get_app_dir()
    try:
        candidates = [
            item
            for item in directory.iterdir()
            if item.is_file() and item.name.casefold() == "url.txt"
        ]
    except OSError:
        return directory / CONFIG_FILENAME

    if candidates:
        # 标准名称优先，其余按名称排序，保证结果稳定。
        candidates.sort(key=lambda item: (item.name != CONFIG_FILENAME, item.name.casefold()))
        return candidates[0]
    return directory / CONFIG_FILENAME


def get_output_path(app_dir: Path | None = None) -> Path:
    return (app_dir or get_app_dir()) / OUTPUT_FILENAME


def get_settings_path(app_dir: Path | None = None) -> Path:
    return (app_dir or get_app_dir()) / SETTINGS_FILENAME


def get_resource_path(relative_path: str) -> Path:
    """读取随 exe 打包的只读资源（例如图标）。"""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / relative_path
    return Path(__file__).resolve().parent.parent / relative_path


def open_local_path(path: Path) -> None:
    """用系统默认程序打开文件或目录。"""
    target = str(path)
    if hasattr(os, "startfile"):
        os.startfile(target)  # type: ignore[attr-defined]
    else:  # pragma: no cover - 当前项目只发布 Windows 版本
        raise OSError("当前系统不支持直接打开本地文件")
