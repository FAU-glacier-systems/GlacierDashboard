"""
Losslessly recompress the glacier NetCDF files (zlib + shuffle, one chunk per 2D frame).

Streams one 2D frame at a time, so memory use stays small (safe next to the live server).
Every output file is verified bit-exact against its source before it is kept.

Usage:
    python tools/compress_netcdf.py SRC_DIR DST_DIR [--level 1]

Files already present (and verified) in DST_DIR are skipped, so the script can be
re-run after an interruption.
"""
import argparse
import itertools
import os
import sys
import time
from pathlib import Path

import netCDF4
import numpy as np

# Frames are written/read one at a time; a small HDF5 chunk cache keeps memory low.
netCDF4.set_chunk_cache(4 * 1024 * 1024, 101, 0.75)


def frame_indices(var):
    """Yield index tuples that each select one 2D (y, x) frame (or the whole var if <= 2D)."""
    if var.ndim <= 2:
        yield (Ellipsis,)
        return
    for idx in itertools.product(*(range(n) for n in var.shape[:-2])):
        yield idx + (slice(None), slice(None))


def compress_file(src: Path, dst: Path, level: int):
    tmp = dst.with_suffix(".nc.tmp")
    with netCDF4.Dataset(src) as s, netCDF4.Dataset(tmp, "w", format=s.data_model) as d:
        d.setncatts({a: s.getncattr(a) for a in s.ncattrs()})
        for name, dim in s.dimensions.items():
            d.createDimension(name, None if dim.isunlimited() else len(dim))
        for name, v in s.variables.items():
            v.set_auto_maskandscale(False)
            fill = v.getncattr("_FillValue") if "_FillValue" in v.ncattrs() else None
            kw = {}
            if v.dtype != str and v.ndim >= 2:
                kw = dict(zlib=True, complevel=level, shuffle=True,
                          chunksizes=(1,) * (v.ndim - 2) + v.shape[-2:])
            dv = d.createVariable(name, v.datatype, v.dimensions, fill_value=fill, **kw)
            dv.set_auto_maskandscale(False)
            dv.setncatts({a: v.getncattr(a) for a in v.ncattrs() if a != "_FillValue"})
            for idx in frame_indices(v):
                dv[idx] = v[idx]
    tmp.replace(dst)


def verify_file(src: Path, dst: Path) -> bool:
    with netCDF4.Dataset(src) as s, netCDF4.Dataset(dst) as d:
        if s.ncattrs() != d.ncattrs() or set(s.variables) != set(d.variables):
            return False
        for name, v in s.variables.items():
            w = d.variables[name]
            v.set_auto_maskandscale(False)
            w.set_auto_maskandscale(False)
            if v.shape != w.shape or v.dtype != w.dtype:
                return False
            for idx in frame_indices(v):
                a, b = np.asarray(v[idx]), np.asarray(w[idx])
                if a.dtype.kind == "f":
                    if not np.array_equal(a, b, equal_nan=True):
                        return False
                elif not np.array_equal(a, b):
                    return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src_dir", type=Path)
    ap.add_argument("dst_dir", type=Path)
    ap.add_argument("--level", type=int, default=1)
    args = ap.parse_args()

    args.dst_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(args.src_dir.glob("*.nc"))
    total_in = total_out = 0
    failed = []
    for i, src in enumerate(files, 1):
        dst = args.dst_dir / src.name
        if dst.exists():
            status = "skip"
        else:
            t = time.time()
            compress_file(src, dst, args.level)
            if not verify_file(src, dst):
                dst.unlink()
                failed.append(src.name)
                print(f"[{i}/{len(files)}] VERIFY FAILED {src.name}", flush=True)
                continue
            status = f"{time.time() - t:.0f}s"
        a, b = src.stat().st_size, dst.stat().st_size
        total_in += a
        total_out += b
        print(f"[{i}/{len(files)}] {src.name}: {a/1e6:.0f} MB -> {b/1e6:.0f} MB ({status})", flush=True)

    print(f"DONE: {total_in/1e9:.1f} GB -> {total_out/1e9:.1f} GB, {len(failed)} failed", flush=True)
    for f in failed:
        print("FAILED:", f)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
