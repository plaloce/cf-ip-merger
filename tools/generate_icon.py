"""生成程序图标；仅在开发/重新打包时使用，运行程序不依赖 Pillow。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)


def lerp(a: int, b: int, ratio: float) -> int:
    return round(a + (b - a) * ratio)


def main() -> None:
    size = 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()

    top = (12, 35, 58)
    bottom = (37, 99, 235)
    for y in range(size):
        ratio_y = y / (size - 1)
        for x in range(size):
            ratio = min(1.0, (x / size) * 0.28 + ratio_y * 0.82)
            pixels[x, y] = (
                lerp(top[0], bottom[0], ratio),
                lerp(top[1], bottom[1], ratio),
                lerp(top[2], bottom[2], ratio),
                255,
            )

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (8, 8, size - 8, size - 8), radius=112, fill=255
    )
    image.putalpha(mask)

    draw = ImageDraw.Draw(image)
    font = None
    for font_path in (
        r"C://Windows//Fonts//msyhbd.ttc",
        r"C://Windows//Fonts//segoeuib.ttf",
        r"C://Windows//Fonts//arialbd.ttf",
    ):
        try:
            font = ImageFont.truetype(font_path, 240)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default(size=240)

    draw.text(
        (size / 2, 222),
        "CF",
        font=font,
        fill=(255, 255, 255, 255),
        anchor="mm",
    )

    # 底部的三路来源汇聚线，兼顾“合并”含义与图标小尺寸可读性。
    draw.rounded_rectangle((112, 377, 400, 385), radius=4, fill=(191, 219, 254, 220))
    for x in (128, 256, 384):
        draw.ellipse((x - 8, 373, x + 8, 389), fill=(255, 255, 255, 255))

    preview = image.resize((256, 256), Image.Resampling.LANCZOS)
    preview.save(ASSETS / "app.png", "PNG")
    preview.save(
        ASSETS / "app.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"图标已生成：{ASSETS / 'app.ico'}")


if __name__ == "__main__":
    main()
