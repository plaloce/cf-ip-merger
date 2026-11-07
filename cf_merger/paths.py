from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_FILENAME = "URL.txt"
SETTINGS_FILENAME = "程序配置.json"
OUTPUT_FILENAME = "优选合集.txt"
RUNTIME_DIRNAME = "CFBestIPMerger"


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


def get_runtime_dir() -> Path | None:
    """返回程序运行期的临时工作目录，取不到系统临时目录时返回 None。

    优先用 %LOCALAPPDATA%\\Temp，其次 TEMP / TMP；都不可用时放弃隔离，
    避免退化成在当前工作目录里新建目录。
    """
    root: Path | None = None
    local = os.environ.get("LOCALAPPDATA")
    if local:
        root = Path(local) / "Temp"
    else:
        for key in ("TEMP", "TMP"):
            value = os.environ.get(key)
            if value:
                root = Path(value)
                break
    if root is None:
        return None
    return root / RUNTIME_DIRNAME / "run"


def isolate_working_directory() -> Path | None:
    """把进程工作目录切到临时目录，避免第三方组件污染程序目录。

    部分显卡驱动等第三方组件会往“当前工作目录”写日志目录（例如
    ``NVIDIA Corporation\\umdlogs``）。本程序所有读写路径都由
    :func:`get_app_dir` 等函数以绝对路径给出，不依赖 cwd，因此切换工作目录
    不影响功能，却能让这类残留落到临时目录，不再出现在源码目录或 exe 旁边。
    """
    target = get_runtime_dir()
    if target is None:
        return None
    try:
        if target.resolve() == Path.cwd().resolve():
            return None
        target.mkdir(parents=True, exist_ok=True)
        os.chdir(target)
    except OSError:
        return None
    return target


def open_local_path(path: Path) -> None:
    """用系统默认程序打开文件或目录。"""
    target = str(path)
    if hasattr(os, "startfile"):
        os.startfile(target)  # type: ignore[attr-defined]
    else:  # pragma: no cover - 当前项目只发布 Windows 版本
        raise OSError("当前系统不支持直接打开本地文件")
