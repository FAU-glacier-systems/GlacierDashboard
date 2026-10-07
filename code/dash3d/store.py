"""Glacier store: the read-optimised data built by tools/build_glacier_store.py (memory-mapped numpy arrays)."""
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer

from . import config

STORE_DIR = config.STORE_DIR

ALPS_BOUNDS = [[float(config.GLACIERS_DF.cenlon.min()) - 0.4, float(config.GLACIERS_DF.cenlat.min()) - 0.3],
               [float(config.GLACIERS_DF.cenlon.max()) + 0.4, float(config.GLACIERS_DF.cenlat.max()) + 0.3]]


def log(*a):
    config.log("[3d]", *a)


TO_UTM = {z: Transformer.from_crs("EPSG:4326", f"EPSG:326{z}", always_xy=True) for z in (31, 32, 33)}
_FROM_UTM = {z: Transformer.from_crs(f"EPSG:326{z}", "EPSG:4326", always_xy=True) for z in (31, 32, 33)}


class Glacier:
    """One glacier in the store. Large arrays are memory-mapped: reads are page-cache lookups."""

    def __init__(self, folder: Path):
        m = json.loads((folder / "meta.json").read_text())
        self.rgi, self.meta = m["rgi"], m
        self.zone, self.dx, self.x0, self.y0 = m["zone"], m["dx"], m["x0"], m["y0"]
        self.nx, self.ny = m["nx"], m["ny"]
        self.topg = np.load(folder / "topg.npy")
        self.weight = np.load(folder / "weight.npy")
        self.thk = np.load(folder / "thk.npy", mmap_mode="r")
        self.folder, self.quant, self._props = folder, m["quant"], {}

        # where this glacier changes the terrain or carries ice: the store already crops the grid to exactly
        # that (footprint plus every cell that ever holds ice), so its extent is the footprint
        xa, xb = self.x0 - self.dx / 2, self.x0 + (self.nx - 0.5) * self.dx
        ya, yb = self.y0 - (self.ny - 0.5) * self.dx, self.y0 + self.dx / 2
        self.foot_utm = (xa, xb, ya, yb)
        inv = _FROM_UTM[self.zone]
        pts = [inv.transform(x, y) for x, y in ((xa, yb), (xb, yb), (xb, ya), (xa, ya))]
        self.foot_bounds = [[min(p[0] for p in pts), min(p[1] for p in pts)],
                            [max(p[0] for p in pts), max(p[1] for p in pts)]]
        self.aspect = self._aspect()

    def _aspect(self):
        """Compass direction the glacier faces (downhill, degrees clockwise from north), from a plane fitted
        through its ice surface in 2000. The map looks at a glacier from that side, so mountains do not hide it."""
        ice_dm = np.asarray(self.thk[0, 0])            # 2000, the same in every scenario
        ice = ice_dm > 0
        if ice.sum() < 10:
            return None
        rows, cols = np.nonzero(ice)
        A = np.c_[cols * self.dx, -rows * self.dx, np.ones(len(rows))]   # x east, y north (rows run south)
        (gx, gy, _), *_ = np.linalg.lstsq(A, (self.topg + ice_dm * 0.1)[ice], rcond=None)
        return round(float(np.degrees(np.arctan2(-gx, -gy)) % 360), 1)

    def prop(self, var):
        """Quantised property array, memory-mapped on first use (each open map holds a file descriptor)."""
        a = self._props.get(var)
        if a is None:
            a = self._props[var] = np.load(self.folder / f"{var}.npy", mmap_mode="r")
        return a

    def mesh_info(self):
        return {"nx": self.nx, "ny": self.ny, "dx": self.dx, "corners": self.meta["corners"],
                "bbox": [c for p in self.foot_bounds for c in p], "aspect": self.aspect}


def _load_store():
    out = {}
    for r in config.GLACIERS_DF.rgi_id:
        try:
            out[r] = Glacier(STORE_DIR / r)
        except Exception as ex:
            log("store entry failed", r, ex)
    if len(out) < len(config.GLACIERS_DF):
        log("WARNING: only", len(out), "of", len(config.GLACIERS_DF), "glaciers loaded from", STORE_DIR)
    return out


GLACIERS = _load_store()
GLACIER_IDS = list(GLACIERS)
FOOT_BOUNDS = np.array([[g.foot_bounds[0][0], g.foot_bounds[0][1], g.foot_bounds[1][0], g.foot_bounds[1][1]]
                        for g in GLACIERS.values()]).reshape(-1, 4)
log("glaciers in store:", len(GLACIERS), "of", len(config.GLACIERS_DF))


def _load_series():
    """Volume (km³) and area (km²) per glacier, scenario and year, computed from the store once and cached."""
    path = STORE_DIR / "series.npz"
    if path.is_file():
        z = np.load(path)
        if list(z["ids"]) == GLACIER_IDS:
            return z["volume"], z["area"]
    log("computing volume/area series from the store (once) ...")
    vol = np.zeros((len(GLACIER_IDS), 3, len(config.YEARS)))
    area = np.zeros_like(vol)
    for k, r in enumerate(GLACIER_IDS):
        g = GLACIERS[r]
        a = np.asarray(g.thk)
        vol[k] = a.sum(axis=(2, 3), dtype=np.int64) * 0.1 * g.dx ** 2 / 1e9
        area[k] = (a > 0).sum(axis=(2, 3)) * g.dx ** 2 / 1e6
    try:
        np.savez(path, ids=np.array(GLACIER_IDS), volume=vol, area=area)
    except OSError as ex:                  # e.g. read-only under systemd; the series still work, just uncached
        log("could not cache the series:", ex)
    return vol, area


SERIES = dict(zip(("volume", "area"), _load_series()))
SERIES_INDEX = {r: k for k, r in enumerate(GLACIER_IDS)}


def glaciers_in(lon0, lat0, lon1, lat1):
    hit = ((FOOT_BOUNDS[:, 0] < lon1) & (FOOT_BOUNDS[:, 2] > lon0) &
           (FOOT_BOUNDS[:, 1] < lat1) & (FOOT_BOUNDS[:, 3] > lat0))
    return [GLACIERS[GLACIER_IDS[i]] for i in np.flatnonzero(hit)]
