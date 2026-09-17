#!/usr/bin/env python3
"""把 regions.json 里某 mode 的所有框画到截图上, 供人工确认/调整。
用法: python vision/hud/draw_regions.py <截图> <mode ally|augment> <输出png>
"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"


def main():
    img_path, mode, out = sys.argv[1], sys.argv[2], sys.argv[3]
    reg = json.loads((HERE / "regions.json").read_text(encoding="utf-8"))[mode]
    img = Image.open(img_path).convert("RGB")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, 18)
    for name, r in reg.items():
        x, y, w, h = r["box"]
        d.rectangle((x, y, x + w, y + h), outline="#ff2d2d", width=2)
        d.text((x, y - 20), f"{name} {r['box']}", font=f, fill="#ffe000")
    img.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
