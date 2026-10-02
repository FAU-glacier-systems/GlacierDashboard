"""Render code/assets/favicon.ico: a small 3D view of the Rhone glacier (year 2000), styled like the dashboard.

Usage (from the repo root):  python tools/make_favicon.py [--preview out.png]
"""
import sys
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from matplotlib.colors import LinearSegmentedColormap, LightSource
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
NC = REPO / "data/glacier_model_data/RGI2000-v7.0-G-11-01706_Projection_output_W5E5_const.nc"
OUT = REPO / "code/assets/favicon.ico"

# same colours as BEDROCK_CS and the "Blues" thickness scale in the dashboard
BEDROCK = LinearSegmentedColormap.from_list("bedrock", ["#303030", "#505050", "#808080", "#b8b8b8", "#d8d8d8"])
ICE = plt.get_cmap("Blues")


def render(size_px=512):
    ds = xr.open_dataset(NC).isel(time=0)
    # crop to the ice plus a margin, so the glacier fills the icon
    iy, ix = np.nonzero(ds["thk"].values > 1)
    my, mx = int(0.12 * np.ptp(iy)), int(0.25 * np.ptp(ix))
    ds = ds.isel(y=slice(max(iy.min() - my, 0), iy.max() + my + 1), x=slice(max(ix.min() - mx, 0), ix.max() + mx + 1))
    topg = ds["topg"].values
    thk = ds["thk"].values
    usurf = np.where(thk > 1, topg + thk, topg)
    x = ds["x"].values
    y = ds["y"].values
    X, Y = np.meshgrid(x, y)

    # colour per cell: hillshaded bedrock, ice coloured by thickness
    rel = (topg - np.nanmin(topg)) / np.ptp(topg[np.isfinite(topg)])
    rgb = BEDROCK(np.nan_to_num(rel))
    ice = thk > 1
    rgb[ice] = ICE(0.35 + 0.65 * np.clip(thk[ice] / np.nanpercentile(thk[ice], 98), 0, 1))
    shade = LightSource(azdeg=315, altdeg=45).hillshade(usurf, vert_exag=1.5, dx=x[1] - x[0], dy=y[1] - y[0])
    rgb[..., :3] *= (0.55 + 0.45 * shade)[..., None]

    fig = plt.figure(figsize=(4, 4), dpi=size_px / 4)
    ax = fig.add_axes([0, 0, 1, 1], projection="3d")
    ax.plot_surface(X, Y, usurf, facecolors=rgb, rstride=1, cstride=1, linewidth=0, antialiased=False, shade=False)
    ax.set_box_aspect((np.ptp(x), np.ptp(y), np.ptp(usurf[np.isfinite(usurf)]) * 2.2))
    ax.view_init(elev=30, azim=-110)   # looking up the glacier from the tongue
    ax.set_axis_off()
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)

    buf = BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    img = Image.open(buf).convert("RGBA")
    return img.crop(img.getbbox())


def square(img):
    side = max(img.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return out


if __name__ == "__main__":
    icon = square(render())
    icon.resize((256, 256), Image.LANCZOS).save(OUT, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("wrote", OUT)
    if "--preview" in sys.argv:
        prev = sys.argv[sys.argv.index("--preview") + 1]
        strip = Image.new("RGBA", (16 + 32 + 64 + 256 + 40, 256), (40, 40, 40, 255))
        xo = 0
        for s in (16, 32, 64, 256):
            strip.alpha_composite(icon.resize((s, s), Image.LANCZOS), (xo, 0))
            xo += s + 10
        strip.save(prev)
        print("preview", prev)
