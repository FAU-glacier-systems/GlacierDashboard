"""
Alps glacier dashboard (3D): what www.glacier-evolution.nat.fau.de serves (see deploy/README.md).
The earlier 2D dashboard, glacier_dashboard_alps.py, still provides shared data and helpers.

A full-window MapLibre GL map:
  - terrain: open AWS Terrain Tiles with the model bedrock (topg) merged in around every glacier. It does not
    change over time, so it is built once and cached on disk.
  - ice: every glacier in view is drawn by map3d.js as its own 3D surface (bedrock + thickness of the chosen
    scenario and year), coloured by the chosen property. A year step fetches one small binary block with the
    thickness and property of all glaciers in view (/api3d/frames).
  - controls, colour bar and time series float over the map and can be hidden.

Data come from the read-optimised store built by tools/build_glacier_store.py (memory-mapped numpy arrays).
Glacier metadata and the time-series chart come from glacier_dashboard_alps.

Run locally:
    DASH_PORT=8060 python glacier_dashboard_3d.py
"""
import gzip
import io
import json
import math
import os
import threading
import urllib.request
from collections import OrderedDict
from pathlib import Path

import numpy as np
import plotly.colors as pc
import plotly.graph_objects as go
from dash import ALL, Dash, dcc, html, Input, Output, State, ctx, no_update
from dash.exceptions import PreventUpdate
from flask import Response, abort, request, send_from_directory
from PIL import Image
from pyproj import Transformer

import glacier_dashboard_alps as base
import legal

CODE_DIR = base.CODE_DIR
ASSETS3D_DIR = CODE_DIR / "assets3d"
MAPLIBRE = "vendor/maplibre-gl-5.24.0"
STORE_DIR = Path(os.environ.get("GLACIER_STORE_DIR", base.DATA_DIR / "glacier_store"))
CACHE_DIR = Path(os.environ.get("GLACIER3D_CACHE_DIR", Path.home() / ".cache" / "glacier3d"))

# Open terrain data (Mapzen/AWS Terrain Tiles, terrarium encoding). Only the server fetches them.
DEM_URL = os.environ.get("DEM_TILE_URL", "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png")
DEM_MAXZOOM = 12            # z12 is about 26 m per pixel in the Alps, close to the 25 m model grid
# The model bedrock is merged into the terrain tiles at every zoom level: the raw DEM still contains the ice
# surface of about 2000, which lies tens of metres above the modelled ice, and MapLibre uses coarse tiles
# for distant parts of a tilted view and while finer tiles load.
TERRAIN_VERSION = "v3"      # part of the tile URL; bump when the merge changes, so all caches start over

# One colour scale per property for all glaciers: (plotly scale, start of the scale used, min, max)
VAR_STYLE = {
    "thk": ("Blues", 0.25, 0.0, 300.0),
    "velsurf_mag": ("Plasma", 0.0, 0.0, 60.0),
    "smb": ("RdBu", 0.0, -5.0, 5.0),
    "mean_temp": ("RdBu_r", 0.0, -8.0, 8.0),
}

ALPS_BOUNDS = [[float(base.GLACIERS_DF.cenlon.min()) - 0.4, float(base.GLACIERS_DF.cenlat.min()) - 0.3],
               [float(base.GLACIERS_DF.cenlon.max()) + 0.4, float(base.GLACIERS_DF.cenlat.max()) + 0.3]]


def log(*a):
    base.log("[3d]", *a)


# =========================
# Glacier store
# =========================
_TO_UTM = {z: Transformer.from_crs("EPSG:4326", f"EPSG:326{z}", always_xy=True) for z in (31, 32, 33)}
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

    def prop(self, var):
        """Quantised property array, memory-mapped on first use (each open map holds a file descriptor)."""
        a = self._props.get(var)
        if a is None:
            a = self._props[var] = np.load(self.folder / f"{var}.npy", mmap_mode="r")
        return a

    def mesh_info(self):
        return {"nx": self.nx, "ny": self.ny, "dx": self.dx, "corners": self.meta["corners"],
                "bbox": [c for p in self.foot_bounds for c in p]}


def _load_store():
    out = {}
    for r in base.GLACIERS_DF.rgi_id:
        folder = STORE_DIR / r
        if (folder / "meta.json").is_file():
            try:
                out[r] = Glacier(folder)
            except Exception as ex:
                log("store entry failed", r, ex)
    if len(out) < len(base.GLACIERS_DF):
        log("WARNING: only", len(out), "of", len(base.GLACIERS_DF), "glaciers loaded from", STORE_DIR)
    return out


GLACIERS = _load_store()
GLACIER_IDS = list(GLACIERS)
FOOT_BOUNDS = np.array([[g.foot_bounds[0][0], g.foot_bounds[0][1], g.foot_bounds[1][0], g.foot_bounds[1][1]]
                        for g in GLACIERS.values()]).reshape(-1, 4)
log("glaciers in store:", len(GLACIERS), "of", len(base.GLACIERS_DF))


def _load_series():
    """Volume (km³) and area (km²) per glacier, scenario and year, computed from the store once and cached."""
    path = STORE_DIR / "series.npz"
    if path.is_file():
        z = np.load(path)
        if list(z["ids"]) == GLACIER_IDS:
            return z["volume"], z["area"]
    log("computing volume/area series from the store (once) ...")
    vol = np.zeros((len(GLACIER_IDS), 3, len(base.YEARS)))
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


# =========================
# Terrain tiles: open DEM with the model bedrock merged in (static, cached on disk)
# =========================
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
    return _TO_UTM[zone].transform(lon, lat)


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


# =========================
# Ice data for the browser
# =========================
def colour_stops(var, n):
    name, start, _, _ = VAR_STYLE[var]
    return pc.sample_colorscale(pc.get_colorscale(name), list(np.linspace(start, 1, n)))


def var_config(var):
    _, _, lo, hi = VAR_STYLE[var]
    offset, scale = (0.0, 0.1) if var == "thk" else next(iter(GLACIERS.values())).quant[var]
    lut = [int(c) for s in colour_stops(var, 256) for c in pc.unlabel_rgb(s)]
    return {"lo": lo, "hi": hi, "offset": offset, "scale": scale, "lut": lut, "label": base.VAR_TO_PROP.get(var, var)}


def _ids_arg():
    """ids=K:stride,K:stride,... -> [(rgi, stride)]. K is the glacier's index in GLACIER_IDS (keeps the URL
    short enough for nginx with hundreds of glaciers in view); the stride (1, 2, 4, 8) is the level of detail."""
    out = []
    for item in request.args.get("ids", "").split(","):
        k, _, s = item.partition(":")
        if not k.isdigit() or int(k) >= len(GLACIER_IDS) or s not in ("1", "2", "4", "8"):
            abort(400)
        out.append((GLACIER_IDS[int(k)], int(s)))
    if not out or len(out) > 400:
        abort(400)
    return out


def _binary(chunks):
    data = gzip.compress(b"".join(chunks), compresslevel=3)
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=86400"})


# =========================
# App
# =========================
# page description and the preview shown when a link is shared (Mastodon, Slack, LinkedIn, messengers)
SITE_URL = os.environ.get("SITE_URL", "https://www.glacier-evolution.nat.fau.de").rstrip("/")
PAGE_TITLE = "Alpine glacier evolution — RGI 11"
PAGE_DESCRIPTION = ("Interactive 3D map of 380 glaciers in the European Alps from 2000 to 2100 under three "
                    "greenhouse gas scenarios (RCP 2.6, 4.5, 8.5): ice thickness, flow speed, mass balance and "
                    "temperature, modelled at FAU Erlangen-Nürnberg.")
PREVIEW_IMAGE = "preview.jpg"                     # 1200 x 630, in code/assets

app = Dash(
    __name__,
    assets_folder=str(CODE_DIR / "assets"),       # shared logos, favicon and base stylesheet
    title=PAGE_TITLE,
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
        {"name": "description", "content": PAGE_DESCRIPTION},
        {"property": "og:type", "content": "website"},
        {"property": "og:site_name", "content": "FAU glacier evolution"},
        {"property": "og:title", "content": "Alpine glacier evolution 2000–2100"},
        {"property": "og:description", "content": PAGE_DESCRIPTION},
        {"property": "og:url", "content": SITE_URL + "/"},
        {"property": "og:image", "content": f"{SITE_URL}/assets/{PREVIEW_IMAGE}"},
        {"property": "og:image:width", "content": "1200"},
        {"property": "og:image:height", "content": "630"},
        {"property": "og:image:alt", "content": "3D view of the Great Aletsch Glacier, coloured by ice thickness"},
        {"name": "twitter:card", "content": "summary_large_image"},
    ],
    index_string=base.INDEX_STRING.replace("<html ", '<html lang="en" ', 1),
    # MapLibre is served from here (assets3d/vendor), so visitors' browsers contact no third party
    external_stylesheets=[f"/static3d/{MAPLIBRE}/maplibre-gl.css", "/static3d/style3d.css"],
    external_scripts=[f"/static3d/{MAPLIBRE}/maplibre-gl.js", "/static3d/map3d.js"],
)
server = app.server
legal.register(server, app.get_asset_url("style.css"))


@server.route("/static3d/<path:name>")
def static3d(name):
    return send_from_directory(ASSETS3D_DIR, name, max_age=60)


@server.route("/api3d/terrain/<version>/<int:z>/<int:x>/<int:y>.png")
def api_terrain(version, z, x, y):
    if version != TERRAIN_VERSION or not (0 <= z <= DEM_MAXZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        abort(404)
    return Response(merged_terrain(z, x, y), mimetype="image/png", headers={"Cache-Control": "public, max-age=604800"})


@server.route("/api3d/beds")
def api_beds():
    """Bedrock of several glaciers (float32, every stride-th cell), concatenated in the order asked."""
    return _binary([np.ascontiguousarray(GLACIERS[i].topg[::s, ::s], dtype="<f4").tobytes() for i, s in _ids_arg()])


@server.route("/api3d/frames")
def api_frames():
    """Ice of several glaciers for one scenario/year: per glacier thickness (uint16, dm) and, unless the
    property is the thickness, the quantised property (uint8, padded to an even length)."""
    ids = _ids_arg()
    scenario, var = request.args.get("scenario"), request.args.get("var")
    try:
        year = int(request.args.get("year", ""))
    except ValueError:
        abort(400)
    if scenario not in base.SCENARIO_TO_IDX or var not in VAR_STYLE or not base.YEARS[0] <= year <= base.YEARS[-1]:
        abort(400)
    si, yi = base.SCENARIO_TO_IDX[scenario], year - base.YEARS[0]
    chunks = []
    for i, s in ids:
        g = GLACIERS[i]
        chunks.append(np.ascontiguousarray(g.thk[si, yi, ::s, ::s], dtype="<u2").tobytes())
        if var != "thk":
            p = np.ascontiguousarray(g.prop(var)[si, yi, ::s, ::s]).tobytes()
            chunks.append(p + (b"\0" if len(p) % 2 else b""))
    return _binary(chunks)


def glaciers_geojson():
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(r.cenlon), float(r.cenlat)]},
        "properties": {"rgi": r.rgi_id, "name": base.glacier_label(r.rgi_id), "country": r.country},
    } for r in base.GLACIERS_DF.itertuples()]}


# all glaciers, named ones first (alphabetically), then the unnamed ones by RGI ID
GLACIER_OPTIONS = sorted(base.glacier_options(None),
                         key=lambda o: (not base.GLACIER_NAMES.get(o["value"]), o["label"].lower()))

# chart metrics, computed from the store (the metrics table of the 2D dashboard predates the current model output)
METRICS = {"volume": "Volume (km³)", "area": "Area (km²)"}

MAP_CONFIG = {
    "alps_bounds": ALPS_BOUNDS,
    "dem_maxzoom": DEM_MAXZOOM,
    "terrain_url": f"/api3d/terrain/{TERRAIN_VERSION}/{{z}}/{{x}}/{{y}}.png",
    "dem_attribution": 'Terrain: <a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md" '
                       'target="_blank">Terrain Tiles</a> (SRTM, GMTED, ETOPO1, EU-DEM © Copernicus, '
                       'DGM © offene Daten Österreichs) · Glacier model: FAU',
    "glaciers": glaciers_geojson(),
    "meshes": {r: {**g.mesh_info(), "k": k} for k, (r, g) in enumerate(GLACIERS.items())},
    "vars": {v: var_config(v) for v in VAR_STYLE},
    "scenario_labels": base.SCENARIO_LABELS,
    "search": [[o["value"], o["label"]] for o in GLACIER_OPTIONS],
    "years": [base.YEARS[0], base.YEARS[-1]],
}

app.layout = html.Div(
    className="app3d",
    children=[
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="url_sync"),
        dcc.Store(id="selected_rgi"),
        dcc.Store(id="property"),          # chosen by clicking the colour bar
        dcc.Store(id="map_config", data=MAP_CONFIG),
        dcc.Store(id="series_data"),       # volume and area of the selection, for the numbers under the colour bar
        dcc.Store(id="theme", data="dark", storage_type="local"),
        dcc.Interval(id="timelapse_interval", interval=250, disabled=True),

        html.Div(id="map3d"),

        # Settings (top left); the body folds away
        html.Section(id="ctrl_panel", className="float float-ctrl is-collapsed", children=[
            html.Div(className="float-head", children=[
                html.Div(className="ellipsis", children=[
                    html.H1("Alpine glacier evolution"),
                    dcc.Dropdown(id="rgi_select", className="title-select", options=[], clearable=True,
                                 placeholder="All glaciers · click to search"),
                ]),
                html.Button("☀", id="theme_toggle", n_clicks=0, className="btn icon-btn",
                            title="Switch light/dark theme", **{"aria-label": "Switch light/dark theme"}),
                html.Button("⚙", id="ctrl_toggle", n_clicks=0, className="btn icon-btn",
                            title="Show or hide the settings", **{"aria-label": "Show or hide the settings"}),
            ]),
            html.Div(id="cbar_btn", className="cbar-btn", n_clicks=0, role="button", tabIndex="0",
                     title="Click to choose what the colours show", children=html.Div(id="colourbar", className="cbar")),
            html.Div(id="prop_menu", className="prop-menu is-hidden", children=[
                html.Button(id={"type": "prop_opt", "index": var}, n_clicks=0, className="prop-opt", children=[
                    html.Span(label, className="prop-opt-label"),
                    html.Span(className="prop-opt-ramp", style={
                        "background": f"linear-gradient(to right, {', '.join(colour_stops(var, 9))})"}),
                ]) for label, var in base.PROP_TO_VAR.items() if var in VAR_STYLE
            ]),
            html.Div(id="glacier_stats", className="stats", **{"aria-live": "polite"}),
            html.Div(className="float-body", children=[
                html.Div(className="fields", children=[
                    base.field("RCP scenario", dcc.Dropdown(
                        id="scenario", options=[{"label": v, "value": k} for k, v in base.SCENARIO_LABELS.items()],
                        clearable=False, searchable=False)),
                ]),
                html.Div(className="chart-block", children=[
                    html.Div(className="chart-head", children=[
                        html.Span(id="chart_scope", className="muted ellipsis"),
                        dcc.Dropdown(id="metric_var_select", className="head-select", clearable=False,
                                     searchable=False, options=[{"label": v, "value": k} for k, v in METRICS.items()]),
                    ]),
                    html.Div(className="ts-legend", children=[
                        html.Span("Historical", className="lg lg-hist"),
                        *[html.Span(label, className=f"lg lg-{key}") for key, label in base.SCENARIO_LABELS.items()],
                    ]),
                    html.Div(className="chart", children=dcc.Graph(
                        id="glacier_timeseries", className="graph", figure=base.empty_fig(),
                        responsive=True, config={"displayModeBar": False})),
                ]),
            ]),
        ]),

        html.Div(className="float map-toolbar", children=[
            html.Button("▶ Play", id="btn_timelapse", n_clicks=0, className="btn"),
            html.Div(className="year", children=dcc.Slider(
                id="year_slider", min=min(base.YEARS), max=max(base.YEARS), step=1, value=base.DEFAULT_YEAR,
                marks={y: str(y) for y in range(min(base.YEARS), max(base.YEARS) + 1, 20)},
                updatemode="drag", tooltip={"placement": "top", "always_visible": False})),
        ]),

        # first-visit hint; map3d.js shows it unless the visitor closed it before
        html.Div(id="intro", className="float intro is-hidden", role="note", children=[
            html.P([
                html.Strong("380 Alpine glaciers, modelled from 2000 to 2100"),
                " under three greenhouse gas scenarios. Press ▶ Play to watch them change, click a glacier "
                "for its numbers, or click the colour bar to show flow speed, mass balance or temperature.",
            ]),
            html.Button("Got it", id="intro_close", className="btn"),
        ]),

        html.Div(className="legal-corner", children=legal.legal_links()),
        html.Div(className="logo-corner", children=[
            html.Img(src=app.get_asset_url(base.LOGO_FAU), className="logo-fau",
                     alt="Friedrich-Alexander-Universität Erlangen-Nürnberg"),
            html.Img(src=app.get_asset_url(base.LOGO_ERC), className="logo-erc",
                     alt="Funded by the European Union · European Research Council"),
        ]),
    ],
)


# =========================
# Client-side: everything that changes per year runs in the browser
# =========================
app.clientside_callback(
    "function(n, t) { return t === 'light' ? 'dark' : 'light'; }",
    Output("theme", "data"), Input("theme_toggle", "n_clicks"), State("theme", "data"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """function(t) {
        t = (t === 'light') ? 'light' : 'dark';
        document.documentElement.dataset.theme = t;
        return t === 'dark' ? '☀' : '☾';
    }""",
    Output("theme_toggle", "children"), Input("theme", "data"),
)

app.clientside_callback(
    """function(n, cls) {
        return cls.includes('is-collapsed') ? cls.replace(' is-collapsed', '') : cls + ' is-collapsed';
    }""",
    Output("ctrl_panel", "className"), Input("ctrl_toggle", "n_clicks"), State("ctrl_panel", "className"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """function(n, picks, cls) {
        const t = window.dash_clientside.callback_context.triggered_id;
        const open = t === 'cbar_btn' && cls.includes('is-hidden');
        return open ? cls.replace(' is-hidden', '') : (cls.includes('is-hidden') ? cls : cls + ' is-hidden');
    }""",
    Output("prop_menu", "className"),
    Input("cbar_btn", "n_clicks"), Input({"type": "prop_opt", "index": ALL}, "n_clicks"), State("prop_menu", "className"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """function(picks) {
        const t = window.dash_clientside.callback_context.triggered_id;
        if (!t || !picks.some(n => n)) return window.dash_clientside.no_update;
        return t.index;
    }""",
    Output("property", "data", allow_duplicate=True),
    Input({"type": "prop_opt", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)

# glacier list: only matches of what is typed (plus the selected glacier, so its name shows)
app.clientside_callback(
    """function(search, value, cfg) {
        const out = [], q = (search || '').trim().toLowerCase();
        if (q) {
            for (const [v, label] of cfg.search) {
                if (v !== value && label.toLowerCase().includes(q)) out.push({label, value: v});
                if (out.length >= 50) break;
            }
        }
        // the selected glacier stays in the list (last), otherwise the dropdown would drop it
        if (value) { const hit = cfg.search.find(o => o[0] === value); if (hit) out.push({label: hit[1], value}); }
        return out;
    }""",
    Output("rgi_select", "options"),
    Input("rgi_select", "search_value"), Input("rgi_select", "value"), State("map_config", "data"),
)

app.clientside_callback(
    """function(glacier, scenario, property, year, metric) {
        const p = new URLSearchParams();
        p.set('glacier', glacier || 'all');
        if (scenario) p.set('scenario', scenario);
        if (property) p.set('property', property);
        if (year) p.set('year', year);
        if (metric) p.set('metric', metric);
        const view = new URLSearchParams(window.location.search).get('view');   // the camera, kept by map3d.js
        if (view) p.set('view', view);
        window.history.replaceState(window.history.state, '', window.location.pathname + '?' + p.toString());
        return window.dash_clientside.no_update;
    }""",
    Output("url_sync", "data"),
    Input("selected_rgi", "data"), Input("scenario", "value"), Input("property", "data"),
    Input("year_slider", "value"), Input("metric_var_select", "value"),
    prevent_initial_call=True,
)

# map, title, colour bar and the chart's year marker
app.clientside_callback(
    """function(rgi, scenario, variable, year, theme, stopped, cfg) {
        const nu = window.dash_clientside.no_update;
        if (!scenario || !variable || year == null) return nu;
        theme = theme === 'light' ? 'light' : 'dark';
        if (window.Map3D) window.Map3D.render({rgi, scenario, variable, year, theme, playing: !stopped}, cfg);
        const v = cfg.vars[variable], lut = v.lut, stops = [];
        for (let i = 0; i <= 8; i++) { const k = Math.round(i / 8 * 255) * 3; stops.push(`rgb(${lut[k]},${lut[k+1]},${lut[k+2]})`); }
        const open = (variable === 'thk' || variable === 'velsurf_mag') ? '+' : '';
        const H = (type, props) => ({namespace: 'dash_html_components', type, props});
        const bar = [
            H('Div', {className: 'cbar-ticks', children: [
                H('Span', {children: String(v.lo)}), H('Span', {className: 'cbar-label', children: v.label}),
                H('Span', {children: String(v.hi) + open})]}),
            H('Div', {className: 'cbar-ramp', style: {background: `linear-gradient(to right, ${stops.join(', ')})`}}),
        ];
        const gd = document.querySelector('#glacier_timeseries .js-plotly-plot');
        if (gd && window.Plotly && gd.layout && gd.offsetParent) {
            const color = theme === 'light' ? '#1a1a1a' : '#e6e6e6';
            window.Plotly.relayout(gd, {shapes: [{type: 'line', xref: 'x', yref: 'paper', x0: year, x1: year, y0: 0, y1: 1,
                                                  line: {color, width: 1, dash: 'dot'}, opacity: 0.6}]});
        }
        return bar;
    }""",
    Output("colourbar", "children"),
    Input("selected_rgi", "data"), Input("scenario", "value"), Input("property", "data"),
    Input("year_slider", "value"), Input("theme", "data"), Input("timelapse_interval", "disabled"),
    State("map_config", "data"),
)

# timelapse: the next year as soon as the map has drawn the current one
app.clientside_callback(
    """function(n, year, cfg) {
        if (window.Map3D && window.Map3D.busy()) return window.dash_clientside.no_update;
        return year >= cfg.years[1] ? cfg.years[0] : year + 1;
    }""",
    Output("year_slider", "value", allow_duplicate=True),
    Input("timelapse_interval", "n_intervals"), State("year_slider", "value"), State("map_config", "data"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """function(n, stopped) { return [!stopped, stopped ? '⏸ Pause' : '▶ Play']; }""",
    Output("timelapse_interval", "disabled"), Output("btn_timelapse", "children"),
    Input("btn_timelapse", "n_clicks"), State("timelapse_interval", "disabled"),
    prevent_initial_call=True,
)


# the numbers under the colour bar: the chosen year against 2000, and what is left in 2100 per scenario
app.clientside_callback(
    """function(series, metric, scenario, year, cfg) {
        if (!series || !scenario || year == null) return [];
        metric = metric === 'area' ? 'area' : 'volume';
        const data = series[metric], unit = metric === 'area' ? 'km²' : 'km³';
        const y0 = cfg.years[0], k = Math.min(Math.max(year - y0, 0), data[0].length - 1);
        const si = Object.keys(cfg.scenario_labels).indexOf(scenario);
        const fmt = (v) => v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v >= 1 ? v.toFixed(2) : v.toPrecision(2);
        const pct = (v, ref) => !(ref > 0) ? '–' : v <= 0 ? 'gone' : v / ref < 0.01 ? '<1 %' : Math.round(v / ref * 100) + ' %';
        const H = (type, props) => ({namespace: 'dash_html_components', type, props});
        const v = data[si][k], ref = data[si][0];
        const now = [H('Strong', {children: String(year)}), ' · ' + (metric === 'area' ? 'area ' : 'volume ') + fmt(v) + ' ' + unit,
                     ...(year > y0 ? [' · ', H('Strong', {children: pct(v, ref)}), ' of 2000'] : [])];
        const last = data[0].length - 1, end = ['Left in ' + cfg.years[1] + ':'];
        Object.entries(cfg.scenario_labels).forEach(([key, label], i) => {
            const t = ' ' + (label + ' ' + pct(data[i][last], data[i][0])).replace(/ /g, '\\u00a0');   // no break inside
            end.push(i === si ? H('Strong', {children: t}) : H('Span', {children: t}));
            if (i < 2) end.push(' ·');
        });
        return [H('Div', {children: now}), H('Div', {className: 'muted', children: end})];
    }""",
    Output("glacier_stats", "children"),
    Input("series_data", "data"), Input("metric_var_select", "value"), Input("scenario", "value"),
    Input("year_slider", "value"), State("map_config", "data"),
)


# =========================
# Server callbacks (selection, URL, chart data)
# =========================
@app.callback(
    Output("scenario", "value"), Output("property", "data"), Output("metric_var_select", "value"),
    Input("url", "search"),
)
def settings_from_url(search):
    q = base.url_params(search)
    scenario = q.get("scenario") if q.get("scenario") in base.SCENARIO_LABELS else "rcp_4_5"
    var = q.get("property") if q.get("property") in VAR_STYLE else base.DEFAULT_VAR
    metric = q.get("metric") if q.get("metric") in METRICS else "volume"
    return scenario, var, metric


@app.callback(
    Output("selected_rgi", "data"), Output("rgi_select", "value"),
    Output("rgi_select", "options", allow_duplicate=True),
    Input("url", "search"), Input("rgi_select", "value"),
    State("selected_rgi", "data"),
    prevent_initial_call="initial_duplicate",
)
def select_glacier(search, rgi_dropdown, current_rgi):
    """Single owner of the selection (None = all glaciers). Map clicks set rgi_select from the browser."""
    if ctx.triggered_id == "rgi_select":
        if rgi_dropdown == current_rgi:
            raise PreventUpdate
        return rgi_dropdown, no_update, no_update
    # no glacier in the URL: the Aletsch glacier; "glacier=all" keeps the all-glaciers view on reload
    rgi = base.url_params(search).get("glacier")
    if rgi == "all":
        rgi = None
    elif rgi not in base.ALL_RGIS:
        rgi = base.DEFAULT_RGI
    # the dropdown drops a value that is not among its options, so send the option along
    return rgi, rgi, ([{"label": base.glacier_label(rgi), "value": rgi}] if rgi else [])


@app.callback(
    Output("year_slider", "value"),
    Input("url", "search"), Input("glacier_timeseries", "clickData"),
)
def set_year(search, ts_click):
    if ctx.triggered_id == "glacier_timeseries":
        try:
            x = float(ts_click["points"][0]["x"])
        except Exception:
            raise PreventUpdate
        return int(min(max(round(x), base.YEARS[0]), base.YEARS[-1]))
    try:
        year = int(base.url_params(search).get("year", base.DEFAULT_YEAR))
    except ValueError:
        year = base.DEFAULT_YEAR
    return year if year in base.YEARS else base.DEFAULT_YEAR


@app.callback(Output("chart_scope", "children"), Input("selected_rgi", "data"))
def chart_scope(rgi):
    return "This glacier" if rgi else f"All {len(GLACIER_IDS)} glaciers"   # the name is in the subtitle


@app.callback(Output("series_data", "data"), Input("selected_rgi", "data"))
def series_data(rgi):
    """Volume and area of the selected glacier (or the sum of all), per scenario and year."""
    out = {}
    for metric, arr in SERIES.items():
        data = arr[SERIES_INDEX[rgi]] if rgi in SERIES_INDEX else arr.sum(axis=0)
        out[metric] = [[float(f"{v:.4g}") for v in row] for row in data]
    return out


def ts_figure(rgi, metric, scenario, theme, year):
    t = base.theme_of(theme)
    arr = SERIES[metric]
    data = arr[SERIES_INDEX[rgi]] if rgi else arr.sum(axis=0)        # (scenario, year)
    years = np.array(base.YEARS)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=years[:21], y=data[0, :21], mode="lines", name="Historical",
                             line={"dash": "dot", "width": 2, "color": t["hist"]}))
    for sc, i in base.SCENARIO_TO_IDX.items():
        selected = sc == scenario
        fig.add_trace(go.Scatter(x=years[20:], y=data[i, 20:], mode="lines", name=base.SCENARIO_LABELS[sc],
                                 line={"width": 2.5 if selected else 1.3, "color": t["rcp"][sc]},
                                 opacity=1.0 if selected else 0.55))
    axis = {"showgrid": True, "gridcolor": t["grid"], "zeroline": False, "linecolor": t["grid"]}
    fig.update_layout(**base.base_layout(
        theme, margin={"l": 4, "r": 8, "t": 4, "b": 4}, hovermode="x unified", showlegend=False,
        xaxis={**axis, "automargin": True, "range": [years[0] - 2, years[-1] + 2], "tickangle": 0, "nticks": 6},
        yaxis={**axis, "automargin": True, "rangemode": "tozero", "nticks": 5},
        shapes=base.year_marker(year, theme),
    ))
    return fig


@app.callback(
    Output("glacier_timeseries", "figure"),
    Input("selected_rgi", "data"), Input("metric_var_select", "value"),
    Input("scenario", "value"), Input("theme", "data"),
    State("year_slider", "value"),
)
def update_timeseries(rgi, metric, scenario, theme, year):
    return ts_figure(rgi, metric if metric in SERIES else "volume", scenario, theme, year)


def _warm_dem():
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


threading.Thread(target=_warm_dem, daemon=True).start()

if __name__ == "__main__":
    app.run(host=os.environ.get("DASH_HOST", "127.0.0.1"), port=int(os.environ.get("DASH_PORT", "8060")),
            debug=False, threaded=True)
