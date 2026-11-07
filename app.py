from __future__ import annotations

import ctypes
import sys


def _configure_windows_process() -> None:
    """在创建 Tk 窗口前启用高分屏支持并设置任务栏标识。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "CFBestIPMerger.Desktop.1"
        )
    except Exception:
        pass


def main() -> None:
    _configure_windows_process()
    from cf_merger.ui import run_app

    run_app()


if __name__ == "__main__":
    main()
