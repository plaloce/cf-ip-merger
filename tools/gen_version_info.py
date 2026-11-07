"""根据 cf_merger.__version__ 生成 PyInstaller 版本资源文件。

版本号只在 cf_merger/__init__.py 中维护一处，这里负责派生出 exe 的
文件属性（右键「属性 → 详细信息」里看到的版本）。生成结果属于构建
中间产物，不纳入版本管理。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cf_merger import APP_NAME, __version__  # noqa: E402

OUTPUT = ROOT / "build_support" / "version_info.txt"

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers},
    prodvers={vers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404b0',
          [
            StringStruct('CompanyName', '本地工具'),
            StringStruct('FileDescription', '{app_name}'),
            StringStruct('FileVersion', '{version}'),
            StringStruct('InternalName', 'CFBestIPMerger'),
            StringStruct('LegalCopyright', 'Copyright (C) 2026'),
            StringStruct('OriginalFilename', 'CF优选IP合并工具.exe'),
            StringStruct('ProductName', '{app_name}'),
            StringStruct('ProductVersion', '{version}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def to_version_tuple(raw: str) -> tuple[int, int, int, int]:
    """把 1.1.1 这类语义化版本转成 PyInstaller 需要的四元组。"""
    parts: list[int] = []
    for chunk in raw.replace("-", ".").replace("+", ".").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return parts[0], parts[1], parts[2], parts[3]


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        TEMPLATE.format(
            vers=to_version_tuple(__version__),
            version=__version__,
            app_name=APP_NAME,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"版本资源已生成：{OUTPUT}（版本 {__version__}）")


if __name__ == "__main__":
    main()
