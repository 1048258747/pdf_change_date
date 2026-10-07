"""把一张方形设计稿转成多尺寸 Windows 图标 assets/app.ico。

设计稿是「圆角方块主体 + 外围渐变底 + 右下角水印」的构图，且外围底色本身
也是蓝色渐变，无法靠饱和度自动分割。这里按归一化比例裁到中间的圆角方块，
再套一层同半径的圆角蒙版让四角透明，最后导出 16~256 的多尺寸 ico。

比例来自 1920x1920 的设计稿实测（主体圆角方块约 x 265~1665 / y 255~1655），
换成别的设计稿时按同样构图重新量一下即可。

运行：
    .venv\\Scripts\\python.exe tools\\make_icon.py [设计稿路径]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "assets" / "icon_source.jpg"
OUT = ROOT / "assets" / "app.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]

# 主体圆角方块在 1920x1920 设计稿中的归一化范围。
CROP = (265 / 1920, 255 / 1920, 1665 / 1920, 1655 / 1920)
# 圆角半径占边长的比例，与设计稿里方块自身的圆角大致对齐。
RADIUS_RATIO = 0.125


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def build(src: Path = DEFAULT_SRC, out: Path = OUT) -> Path:
    if not src.exists():
        raise SystemExit(f"找不到设计稿：{src}")

    img = Image.open(src).convert("RGBA")
    w, h = img.size
    box = (round(CROP[0] * w), round(CROP[1] * h), round(CROP[2] * w), round(CROP[3] * h))

    tile = img.crop(box)
    side = max(tile.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(tile, ((side - tile.width) // 2, (side - tile.height) // 2))
    square.putalpha(_rounded_mask(side, max(1, round(side * RADIUS_RATIO))))

    frames = [square.resize((s, s), Image.LANCZOS) for s in SIZES]
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"设计稿 {w}x{h} → 裁剪 {box} → {out.name}"
          f"（{out.stat().st_size / 1024:.1f} KB，{len(SIZES)} 种尺寸）")
    return out


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC)