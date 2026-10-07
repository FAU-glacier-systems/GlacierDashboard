"""Terrain tiles: open DEM with the model bedrock merged in. They do not change over time, so they are built once
and cached on disk."""
import io
import math
import os
import threading
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from .store import ALPS_BOUNDS, TO_UTM, glaciers_in, log

CACHE_DIR = Path(os.environ.get("GLACIER3D_CACHE_DIR", Path.home() / ".cache" / "glacier3d"))

# Open terrain data (Mapzen/AWS Terrain Tiles, terrarium encoding). Only the server fetches them.
DEM_URL = os.environ.get("DEM_TILE_URL", "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png")
DEM_MAXZOOM = 12            # z12 is about 26 m per pixel in the Alps, close to the 25 m model grid
# The model bedrock is merged into the terrain tiles at every zoom level: the raw DEM still contains the ice
# surface of about 2000, which lies tens of metres above the modelled ice, and MapLibre uses coarse tiles
# for distant parts of a tilted view and while finer tiles load.
TERRAIN_VERSION = "v3"      # part of the tile URL; bump when the merge changes, so all caches start over


def tile_bounds(z, x, y):
    n = 2 ** z
    lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon0, lat0, lon1, lat1


def tile_utm(z, x, y, zone, size=256):
    n = size * 2 ** z
    px = (x * size + np.arange(size) + 0.5) / n
    py = (y * size + np.arange(size) + 0.5) / n
    lon, lat = np.meshgrid(px * 360.0 - 180.0, np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py)))))
    return TO_UTM[zone].transform(lon, lat)


def bilinear(a, fi, fj):
    ny, nx = a.shape
    i0 = np.clip(np.floor(fi).astype(int), 0, nx - 2)
    j0 = np.clip(np.floor(fj).astype(int), 0, ny - 2)
    tx, ty = np.clip(fi - i0, 0, 1), np.clip(fj - j0, 0, 1)
    return ((a[j0, i0] * (1 - tx) + a[j0, i0 + 1] * tx) * (1 - ty) +
            (a[j0 + 1, i0] * (1 - tx) + a[j0 + 1, i0 + 1] * tx) * ty)


def _write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def dem_tile_bytes(z, x, y):
    """Terrarium PNG from the open DEM, cached on disk."""
    path = CACHE_DIR / "terrarium" / str(z) / str(x) / f"{y}.png"
    if path.is_file():
        return path.read_bytes()
    req = urllib.request.Request(DEM_URL.format(z=z, x=x, y=y), headers={"User-Agent": "glacier-evolution.nat.fau.de"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    _write_atomic(path, data)
    return data


def decode_terrarium(data):
    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


def encode_terrarium(elev):
    v = np.clip(elev, -11000, 9000).astype(np.float64) + 32768
    r = np.floor(v / 256)
    g = np.floor(v - r * 256)
    b = np.floor((v - r * 256 - g) * 256)
    buf = io.BytesIO()
    Image.fromarray(np.stack([r, g, b], axis=-1).astype(np.uint8), "RGB").save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def despike(elev, z, y):
    """Replace outliers in a DEM tile by the median of their 5x5 neighbourhood.

    The source tiles hold seams of bad pixels (e.g. at zoom 11-12 next to Jamtalferner: -2300 m to 20900 m).
    Real terrain stays within about 2.5 pixel widths of that median even at the Matterhorn, so 3 pixel
    widths (at least 150 m) only catches the artefacts."""
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 2 ** z))))
    thr = max(150.0, 3 * 40075016 * math.cos(math.radians(lat)) / 256 / 2 ** z)
    for _ in range(2):                                    # a second pass for clusters that skewed the first median
        p = np.pad(elev, 2, mode="edge")
        med = np.nanmedian(np.lib.stride_tricks.sliding_window_view(p, (5, 5)), axis=(-2, -1))
        bad = (np.abs(elev - med) > thr) | (elev > 4900)  # 4900 m: above Mont Blanc
        if not bad.any():
            break
        elev = np.where(bad, med, elev)
    return elev


def merged_terrain(z, x, y):
    """Terrain tile: the open DEM without its spikes, with the bedrock of every glacier footprint merged in."""
    path = CACHE_DIR / f"terrain_{TERRAIN_VERSION}" / str(z) / str(x) / f"{y}.png"
    if path.is_file():
        return path.read_bytes()
    elev = despike(decode_terrarium(dem_tile_bytes(z, x, y)), z, y)
    utm = {}
    for g in glaciers_in(*tile_bounds(z, x, y)):
        if g.zone not in utm:                 # coarse tiles hold many glaciers: convert once per zone
            utm[g.zone] = tile_utm(z, x, y, g.zone)
        X, Y = utm[g.zone]
        xa, xb, ya, yb = g.foot_utm
        hit = (X >= xa) & (X <= xb) & (Y >= ya) & (Y <= yb)
        rows, cols = np.flatnonzero(hit.any(axis=1)), np.flatnonzero(hit.any(axis=0))
        if not len(rows):
            continue
        win = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
        fi = (X[win] - g.x0) / g.dx
        fj = (g.y0 - Y[win]) / g.dx
        inside = (fi >= 0) & (fi <= g.nx - 1) & (fj >= 0) & (fj <= g.ny - 1)
        w = np.where(inside, bilinear(g.weight, fi, fj), 0)
        if not (w > 0).any():
            continue
        bed = bilinear(g.topg, fi, fj)
        w = np.where(np.isfinite(bed), w, 0)
        elev[win] = elev[win] * (1 - w) + np.nan_to_num(bed) * w
    data = encode_terrarium(elev)
    _write_atomic(path, data)
    return data


def warm_dem():
    """Fetch the overview zoom levels of the whole Alps once, so the first visit is not slow."""
    (w, s), (e, n) = ALPS_BOUNDS
    fetched = 0
    for z in range(5, 11):
        k = 2 ** z
        x0, x1 = int((w + 180) / 360 * k), int((e + 180) / 360 * k)
        y0 = int((1 - math.asinh(math.tan(math.radians(n))) / math.pi) / 2 * k)
        y1 = int((1 - math.asinh(math.tan(math.radians(s))) / math.pi) / 2 * k)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                try:
                    if not (CACHE_DIR / "terrarium" / str(z) / str(x) / f"{y}.png").is_file():
                        dem_tile_bytes(z, x, y)
                        fetched += 1
                    merged_terrain(z, x, y)            # cached on disk after the first worker built it
                except Exception:
                    pass
    log("terrain warm-up done,", fetched, "DEM tiles fetched")
