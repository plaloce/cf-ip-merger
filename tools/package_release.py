"""把 dist 目录中的发布文件整理成可直接分发的 zip。"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "dist"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cf_merger import __version__  # noqa: E402  版本号统一读源码，避免多处手改

VERSION = __version__
FILES = [
    RELEASE / "CF优选IP合并工具.exe",
    RELEASE / "URL.txt",
    RELEASE / "程序配置.json",
    RELEASE / "README.md",
]


def main() -> None:
    missing = [path for path in FILES if not path.exists()]
    if missing:
        names = "、".join(path.name for path in missing)
        raise SystemExit(f"发布文件不完整，缺少：{names}")

    archive = RELEASE / f"CF优选IP合并工具_v{VERSION}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in FILES:
            bundle.write(path, arcname=path.name)
    size_mb = archive.stat().st_size / 1024 / 1024
    print(f"发布包已生成：{archive}（{size_mb:.1f} MB）")


if __name__ == "__main__":
    main()
