"""Make the site icons from the FRAGILE logo (tools/fragile_logo.png, transparent background):
favicon.ico, icon-512.png and apple-touch-icon.png.

Usage (from the repo root):  python tools/make_favicon.py [--preview out.png]
"""
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "tools/fragile_logo.png"
ASSETS = REPO / "code/assets"


def badge():
    """The logo centred on a transparent square, cropped tight."""
    img = Image.open(SRC).convert("RGBA")
    img = img.crop(img.getbbox())
    side = max(img.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return out


if __name__ == "__main__":
    icon = badge()
    icon.resize((256, 256), Image.LANCZOS).save(ASSETS / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    icon.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon-512.png", optimize=True)
    # iOS shows transparency as black and rounds the corners itself, so the home screen icon gets a white square
    touch = Image.new("RGBA", (180, 180), (255, 255, 255, 255))
    touch.alpha_composite(icon.resize((168, 168), Image.LANCZOS), (6, 6))
    touch.convert("RGB").save(ASSETS / "apple-touch-icon.png", optimize=True)
    print("wrote favicon.ico, icon-512.png, apple-touch-icon.png in", ASSETS)
    if "--preview" in sys.argv:
        prev = sys.argv[sys.argv.index("--preview") + 1]
        strip = Image.new("RGBA", (16 + 32 + 64 + 256 + 40, 256), (40, 40, 40, 255))
        xo = 0
        for s in (16, 32, 64, 256):
            strip.alpha_composite(icon.resize((s, s), Image.LANCZOS), (xo, 0))
            xo += s + 10
        strip.save(prev)
        print("preview", prev)
