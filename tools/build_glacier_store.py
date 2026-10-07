"""
Build the read-optimised glacier store used by the 3D dashboard (code/dash3d/store.py).

The NetCDF files are compressed per 2D frame, so every frame read means decompressing it (several ms).
The store keeps only what the 3D view draws, uncompressed and memory-mappable, cropped to the
glacier's footprint and north-up. Per glacier, in data/glacier_store/<RGI-ID>/:

    meta.json         grid geometry, UTM zone, quantisation
    topg.npy          float32 (ny, nx)            bedrock, constant over time and scenarios
    weight.npy        float32 (ny, nx)            how much the model bedrock replaces the DEM in the terrain
    thk.npy           uint16  (3, 101, ny, nx)    ice thickness in dm (0 = no ice); scenario x year 2000-2100
    velsurf_mag.npy   uint8   (3, 101, ny, nx)    quantised properties, value = offset + q * scale,
    smb.npy                                       q = 255 means no data
    mean_temp.npy

The ice surface is topg + thk (exact in the model output). Years 2000-2019 come from the W5E5 run
(the same for every scenario), 2020-2100 from the CORDEX run.

Usage (from the repo root):
    python tools/build_glacier_store.py [--nc-dir DIR] [--out DIR] [RGI-ID ...]
Glaciers already in the store are skipped, so the script can be re-run after an interruption.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd
from pyproj import Transformer

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = ["rcp_2_6", "rcp_4_5", "rcp_8_5"]          # order of the 'experiment' dimension
YEARS = list(range(2000, 2101))
ICE_MIN_THK = 0.5                                      # m; thinner ice counts as gone
FOOTPRINT_DILATE = 4                                   # cells around the initial ice outline
FOOTPRINT_BLUR = 3
QUANT = {                                              # value = offset + q * scale, q = 0..254
    "velsurf_mag": (0.0, 1.0),                         # 0..254 m/a
    "smb": (-12.7, 0.1),                               # -12.7..12.7 m w.e./a
    "mean_temp": (-25.4, 0.2),                         # -25.4..25.4 degC
}

netCDF4.set_chunk_cache(4 * 1024 * 1024, 101, 0.75)
TO_UTM = {z: Transformer.from_crs("EPSG:4326", f"EPSG:326{z}", always_xy=True) for z in (31, 32, 33)}
FROM_UTM = {z: Transformer.from_crs(f"EPSG:326{z}", "EPSG:4326", always_xy=True) for z in (31, 32, 33)}


def dilate(m, n):
    out = m.copy()
    for _ in range(n):
        g = out.copy()
        g[1:] |= out[:-1]; g[:-1] |= out[1:]
        g[:, 1:] |= out[:, :-1]; g[:, :-1] |= out[:, 1:]
        out = g
    return out


def box_blur(a, r):
    k = 2 * r + 1
    p = np.pad(a, r, mode="edge")
    c = np.cumsum(np.cumsum(np.pad(p, ((1, 0), (1, 0))), 0), 1)
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def read(ds, var):
    v = ds[var]
    v.set_auto_mask(False)
    a = np.asarray(v[:], dtype=np.float32)
    fill = getattr(v, "_FillValue", None)
    if fill is not None:
        a[a == np.float32(fill)] = np.nan
    return a


def stack_years(w5e5, cordex):
    """(21, ny, nx) W5E5 2000-2020 + (3, 81, ny, nx) CORDEX 2020-2100 -> (3, 101, ny, nx)."""
    out = np.empty((3, len(YEARS)) + cordex.shape[-2:], dtype=np.float32)
    out[:, :20] = w5e5[None, :20]
    out[:, 20:] = cordex
    return out


def build(rgi, lon, lat, nc_dir: Path, out_dir: Path):
    cordex = netCDF4.Dataset(nc_dir / f"{rgi}_Projection_CORDEX_output_2D.nc")
    w5e5 = netCDF4.Dataset(nc_dir / f"{rgi}_Projection_output_W5E5_const.nc")
    for d in (cordex, w5e5):
        years = np.asarray(d["time"][:]).reshape(-1, np.asarray(d["time"][:]).shape[-1])[0]
        assert (d is cordex and list(years.astype(int)) == YEARS[20:]) or \
               (d is w5e5 and list(years.astype(int)) == YEARS[:21]), f"unexpected years in {d.filepath()}"

    xs = np.asarray(w5e5["x"][:], float)
    ys = np.asarray(w5e5["y"][:], float)
    topg = read(w5e5, "topg")[0]
    init = np.nan_to_num(read(w5e5, "icemask_init")[0]) > 0.5

    thk = stack_years(read(w5e5, "thk"), read(cordex, "thk"))
    mask = stack_years(read(w5e5, "icemask"), read(cordex, "icemask"))
    ice = np.isfinite(thk) & (thk > ICE_MIN_THK) & (np.nan_to_num(mask) > 0.5)
    del mask
    thk_dm = np.where(ice, np.clip(np.rint(thk * 10), 0, 65535), 0).astype(np.uint16)
    del thk

    weight = box_blur(dilate(init, FOOTPRINT_DILATE).astype(np.float32), FOOTPRINT_BLUR)
    weight[:2], weight[-2:], weight[:, :2], weight[:, -2:] = 0, 0, 0, 0

    # crop to the footprint plus any cell that ever holds ice
    keep = (weight > 0) | ice.any(axis=(0, 1))
    rows, cols = np.flatnonzero(keep.any(axis=1)), np.flatnonzero(keep.any(axis=0))
    r0, r1 = max(rows[0] - 1, 0), min(rows[-1] + 2, len(ys))
    c0, c1 = max(cols[0] - 1, 0), min(cols[-1] + 2, len(xs))
    crop = (slice(r0, r1), slice(c0, c1))
    xs, ys = xs[c0:c1], ys[r0:r1]
    north_up = ys[0] < ys[-1]

    def fix(a):
        a = a[(...,) + crop]
        return np.ascontiguousarray(a[..., ::-1, :] if north_up else a)

    if north_up:
        ys = ys[::-1]

    zone = 32
    for z, t in TO_UTM.items():
        X, Y = t.transform(lon, lat)
        if xs.min() <= X <= xs.max() and ys.min() <= Y <= ys.max():
            zone = z
            break
    inv = FROM_UTM[zone]
    dx = float(xs[1] - xs[0])
    corners = [list(inv.transform(x, y)) for x, y in ((xs[0], ys[0]), (xs[-1], ys[0]), (xs[-1], ys[-1]), (xs[0], ys[-1]))]

    tmp = out_dir / f"{rgi}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    np.save(tmp / "topg.npy", fix(topg).astype(np.float32))
    np.save(tmp / "weight.npy", fix(weight).astype(np.float32))
    np.save(tmp / "thk.npy", fix(thk_dm))
    for var, (offset, scale) in QUANT.items():
        a = stack_years(read(w5e5, var), read(cordex, var))
        q = np.where(np.isfinite(a), np.clip(np.rint((a - offset) / scale), 0, 254), 255).astype(np.uint8)
        np.save(tmp / f"{var}.npy", fix(q))
        del a, q
    meta = {
        "rgi": rgi, "zone": zone, "dx": dx,
        "x0": float(xs[0]), "y0": float(ys[0]),          # centre of the top-left cell (UTM)
        "nx": len(xs), "ny": len(ys),
        "corners": corners,                               # cell centres TL, TR, BR, BL as lon/lat
        "scenarios": SCENARIOS, "years": [YEARS[0], YEARS[-1]],
        "thk_scale": 0.1, "quant": QUANT,
    }
    (tmp / "meta.json").write_text(json.dumps(meta))
    cordex.close()
    w5e5.close()
    final = out_dir / rgi
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    return len(xs) * len(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nc-dir", type=Path, default=REPO / "data" / "glacier_model_data")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "glacier_store")
    ap.add_argument("--csv", type=Path, default=REPO / "data" / "glacier_location_and_name" / "glaciers_region11_alps.csv")
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args()

    df = pd.read_csv(args.csv, encoding="utf-8-sig")
    if args.ids:
        df = df[df.rgi_id.isin(args.ids)]
    args.out.mkdir(parents=True, exist_ok=True)
    failed = []
    t_all = time.time()
    for i, r in enumerate(df.itertuples(), 1):
        if (args.out / r.rgi_id / "meta.json").is_file():
            continue
        t = time.time()
        try:
            cells = build(r.rgi_id, float(r.cenlon), float(r.cenlat), args.nc_dir, args.out)
            print(f"[{i}/{len(df)}] {r.rgi_id}: {cells} cells, {time.time() - t:.1f}s", flush=True)
        except Exception as ex:
            failed.append(r.rgi_id)
            print(f"[{i}/{len(df)}] {r.rgi_id}: FAILED {ex!r}", flush=True)
    print(f"DONE in {time.time() - t_all:.0f}s, {len(failed)} failed", flush=True)
    for f in failed:
        print("FAILED:", f)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
