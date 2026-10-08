"""
Build the list of mountain peaks for the 3D dashboard's search (data/peaks/peaks_region11_alps.json).

Peaks come from OpenStreetMap (natural=peak with a name and a Wikidata entry, i.e. notable ones) within
MAX_KM of a modelled glacier's ice (in 2000). Picking one in the search puts the camera on the summit, looking
at the nearest such glacier: at the point of its ice that is closest to the ice's middle while lying at least
MIN_DOWN degrees below the summit (peaks without such a glacier are left out, the view would look up at a
slope). Both heights are taken from the dashboard's own terrain tiles (zoom 12), so
the camera sits just above the ground the browser draws, not where OpenStreetMap's "ele" says:

    cam   [lon, lat, m]   the summit, CAM_ABOVE m above the highest terrain within about 60 m of the peak
    look  [lon, lat, m]   that point on the terrain (bedrock, the ice is drawn on top)

Each peak also gets "label": true for the most prominent and high LABEL_SHARE of them, the ones the map labels
(all stay in the search). The score is the peak's isolation (km to the nearest higher summit in OpenStreetMap,
ignoring points within 150 m, which are the same summit mapped twice) times the square of its height in km, so
both a lone summit and a high one rank well.

The point must be visible: the line of sight has to clear the terrain (and the ice of 2000) by CLEARANCE. If no
ice is visible from 40 m above the summit, the camera goes higher (CAM_ABOVE).

The data are © OpenStreetMap contributors, ODbL (credited on the map's © link and in the Impressum).

The Overpass API answers are cached per 0.25° cell in ~/.cache/glacier3d/osm_peaks/, so the script can be
re-run after an interruption (public Overpass servers are often busy) without fetching everything again;
--refresh fetches anew.

Usage (from the repo root, with the dashboard's Python environment):
    python tools/build_peaks.py [--refresh]
"""
import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "code"))
from dash3d import config                                                         # noqa: E402
from dash3d.store import _FROM_UTM, GLACIERS, TO_UTM                              # noqa: E402
from dash3d.terrain import (CACHE_DIR, DEM_MAXZOOM, decode_terrarium, dem_tile_bytes, despike,  # noqa: E402
                            merged_terrain)

OUT = config.DATA_DIR / "peaks" / "peaks_region11_alps.json"
OSM_CACHE = CACHE_DIR / "osm_peaks"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
CELL = 0.25          # degrees; one Overpass query per cell that a glacier's footprint (plus MARGIN) touches
MARGIN = 0.05        # degrees, more than MAX_KM
MAX_KM = 3.0         # peaks further than this from the ice of every modelled glacier are left out
MIN_DOWN = 3.0       # degrees: the glacier looked at must lie at least this far below the camera
CAM_ABOVE = (40, 80, 150)   # m above the summit terrain; the next height only if no ice is visible from the last
CLEARANCE = 10       # m: the line of sight to the point looked at must pass at least this far above the terrain
LABEL_SHARE = 0.10   # of the peaks are labelled on the map
NAME_TAGS = ("name:de", "name:it", "name:fr", "name:en", "name:rm", "name:sl", "alt_name", "old_name")


def overpass(s, w, n, e):
    """Named peaks in the box, or None if no server answered."""
    query = f"[out:json][timeout:90];node[natural=peak][name]({s},{w},{n},{e});out body;"
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": query}).encode(),
                                         headers={"User-Agent": "glacier-evolution.nat.fau.de peak list"})
            data = json.loads(urllib.request.urlopen(req, timeout=150).read())
            time.sleep(1)                                   # be gentle with the public servers
            return data["elements"]
        except Exception as ex:                             # busy server, timeout, an HTML error page
            print(f"  {url.split('/')[2]} failed ({str(ex)[:60]})", flush=True)
    return None


def fetch_cell(cx, cy, refresh):
    path = OSM_CACHE / f"{cx}_{cy}.json"
    if path.is_file() and not refresh:
        return json.loads(path.read_text())["elements"]
    s, w = cy * CELL - MARGIN, cx * CELL - MARGIN
    n, e = (cy + 1) * CELL + MARGIN, (cx + 1) * CELL + MARGIN
    for attempt in range(3):
        elements = overpass(s, w, n, e)
        if elements is None:                                # a dense cell can time out: try it in quarters
            ms, mw = (s + n) / 2, (w + e) / 2
            parts = [overpass(*b) for b in ((s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e))]
            elements = None if None in parts else [x for part in parts for x in part]
        if elements is not None:
            OSM_CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"elements": elements}))
            return elements
        time.sleep(30 * (attempt + 1))
    raise SystemExit(f"cell {cx},{cy}: no Overpass server answered; run again later")


def km(lon1, lat1, lon2, lat2):
    return 6371 * math.hypot(math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)),
                             math.radians(lat2 - lat1))


class Terrain:
    """Heights from the dashboard's zoom-12 terrain tiles: the merged one the browser draws and the raw DEM
    (which still holds the ice surface of about 2000, so an ice-capped summit is not too low)."""
    def __init__(self):
        self.z, self.tiles = DEM_MAXZOOM, {}

    def _pixel(self, lon, lat):
        n = 256 * 2 ** self.z
        px = (lon + 180) / 360 * n
        py = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        return px, py

    def _tile(self, x, y):
        if (x, y) not in self.tiles:
            merged = decode_terrarium(merged_terrain(self.z, x, y))
            raw = despike(decode_terrarium(dem_tile_bytes(self.z, x, y)), self.z, y)
            self.tiles[(x, y)] = np.maximum(merged, raw), merged
        return self.tiles[(x, y)]

    def _window(self, lon, lat, r, which):
        px, py = self._pixel(lon, lat)
        vals = []
        for j in range(int(py) - r, int(py) + r + 1):
            for i in range(int(px) - r, int(px) + r + 1):
                vals.append(self._tile(i // 256, j // 256)[which][j % 256, i % 256])
        return vals

    def summit(self, lon, lat):
        return float(max(self._window(lon, lat, 2, 0)))         # 5x5 pixels, about 130 m across

    def ground(self, lon, lat):
        return float(self._window(lon, lat, 0, 1)[0])

    def surface(self, lon, lat):
        """Highest of drawn terrain and the 2000 ice surface at many points (arrays), nearest pixel."""
        n = 256 * 2 ** self.z
        px = ((np.asarray(lon) + 180) / 360 * n).astype(int)
        py = ((1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n).astype(int)
        out = np.empty(px.shape, dtype=np.float32)
        tiles = (px // 256) * 100000 + py // 256
        for t in np.unique(tiles):
            m = tiles == t
            out[m] = self._tile(int(t // 100000), int(t % 100000))[0][py[m] % 256, px[m] % 256]
        return out

    def visible(self, cam, look):
        """Whether the line of sight from cam to look ([lon, lat, m]) clears the terrain by CLEARANCE, apart from
        the first 60 m (the summit under the camera) and the last 150 m (the slope around the point)."""
        dist = km(cam[0], cam[1], look[0], look[1]) * 1000
        f = np.arange(60, dist - 150, 20) / dist
        if not len(f):
            return True
        line = cam[2] + f * (look[2] - cam[2])
        ground = self.surface(cam[0] + f * (look[0] - cam[0]), cam[1] + f * (look[1] - cam[1]))
        return bool((ground <= line - CLEARANCE).all())


def ice_points(g):
    """The glacier's ice cells in 2000 (every second cell, 50 m apart): UTM x, y, bedrock height, and the squared
    distance to the middle of the ice."""
    rows, cols = np.nonzero(np.asarray(g.thk[0, 0])[::2, ::2] > 0)
    x, y = g.x0 + cols * 2 * g.dx, g.y0 - rows * 2 * g.dx
    return x, y, g.topg[::2, ::2][rows, cols], (x - x.mean()) ** 2 + (y - y.mean()) ** 2


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="fetch from Overpass again instead of the cache")
    args = ap.parse_args()

    cells = set()
    for g in GLACIERS.values():
        (w, s), (e, n) = g.foot_bounds
        cells |= {(cx, cy) for cx in range(int((w - MARGIN) // CELL), int((e + MARGIN) // CELL) + 1)
                  for cy in range(int((s - MARGIN) // CELL), int((n + MARGIN) // CELL) + 1)}
    nodes = {}
    for k, (cx, cy) in enumerate(sorted(cells)):
        print(f"cell {k + 1}/{len(cells)}", flush=True)
        for e in fetch_cell(cx, cy, args.refresh):
            nodes[e["id"]] = e

    ice = {r: ice_points(g) for r, g in GLACIERS.items()}
    terrain, peaks, below = Terrain(), [], 0
    for e in nodes.values():
        t = e["tags"]
        if t.get("natural") != "peak" or not t.get("name") or not t.get("wikidata"):
            continue
        near = []                                           # (km to the ice, rgi) of the glaciers within MAX_KM
        for r, g in GLACIERS.items():
            (w, s), (east, n) = g.foot_bounds
            if not (w - MARGIN <= e["lon"] <= east + MARGIN and s - MARGIN <= e["lat"] <= n + MARGIN):
                continue
            px, py = TO_UTM[g.zone].transform(e["lon"], e["lat"])
            x, y = ice[r][:2]
            if len(x):
                dist = np.hypot(x - px, y - py)
                if dist.min() <= MAX_KM * 1000:
                    near.append((float(dist.min()), r, dist))
        if not near:
            continue
        summit, look = terrain.summit(e["lon"], e["lat"]), None
        for above in CAM_ABOVE:                             # higher only if nothing is visible from lower down
            cam_alt = summit + above
            for _, r, dist in sorted(near, key=lambda n: n[0]):     # the nearest glacier with visible ice below
                x, y, bed, mid = ice[r]
                ok = np.flatnonzero((dist > 100) & (np.degrees(np.arctan2(cam_alt - bed, dist)) >= MIN_DOWN))
                ok = ok[np.argsort(mid[ok])]                # nearest the middle first; at most 300 tried, evenly
                for k in ok[::max(1, len(ok) // 300)]:
                    glon, glat = _FROM_UTM[GLACIERS[r].zone].transform(x[k], y[k])
                    ground = terrain.ground(glon, glat)
                    if terrain.visible((e["lon"], e["lat"], cam_alt), (glon, glat, ground)):
                        look = (r, glon, glat, ground)
                        break
                if look:
                    break
            if look:
                break
        if look is None:
            below += 1
            continue
        try:
            ele = round(float(t.get("ele", "").replace(",", ".").split(";")[0].removesuffix("m").strip()))
        except ValueError:
            ele = None
        alt = sorted({t[k].strip() for k in NAME_TAGS if t.get(k, "").strip() and t[k].strip() != t["name"]})
        peaks.append({
            "id": f"osm{e['id']}", "name": t["name"].strip(), "alt": alt, "ele": ele,
            "cam": [round(e["lon"], 5), round(e["lat"], 5), round(cam_alt)],
            "look": [round(look[1], 5), round(look[2], 5), round(look[3])],
            "glacier": look[0],
        })
    # map labels: the most prominent and high peaks (isolation x height squared)
    summits = []
    for e in nodes.values():
        try:
            summits.append((e["lon"], e["lat"], float(e["tags"].get("ele", "").replace(",", ".").split(";")[0]
                                                         .removesuffix("m").strip())))
        except ValueError:
            pass
    summits = np.array(summits)
    for p in peaks:
        ele = p["ele"] or p["cam"][2] - CAM_ABOVE[0]
        hi = summits[summits[:, 2] > ele]
        d = np.hypot((hi[:, 0] - p["cam"][0]) * math.cos(math.radians(p["cam"][1])), hi[:, 1] - p["cam"][1]) * 111.32
        d = d[d > 0.15]
        p["score"] = round(float(d.min() if len(d) else 100) * (ele / 1000) ** 2, 1)
    cut = sorted((p["score"] for p in peaks), reverse=True)[int(len(peaks) * LABEL_SHARE)]
    for p in peaks:
        p["label"] = p["score"] > cut
    peaks.sort(key=lambda p: (p["name"].lower(), p["id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)",
        "built": time.strftime("%Y-%m-%d"),
        "rule": f"natural=peak with name and wikidata, within {MAX_KM:g} km of a modelled glacier's ice, "
                f"looking at visible ice of the nearest one at least {MIN_DOWN:g}° below the summit",
        "peaks": peaks,
    }, ensure_ascii=False, indent=0))
    print(f"{len(peaks)} peaks -> {OUT} ({below} left out: no visible glacier below them)")


if __name__ == "__main__":
    main()
