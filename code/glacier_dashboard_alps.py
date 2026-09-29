import os, re, sys, math, base64, hashlib, threading
from pathlib import Path
from urllib.parse import parse_qs

import numpy as np
import pandas as pd
import xarray as xr
from collections import OrderedDict

from dash import Dash, dcc, html, Input, Output, State, Patch, ctx, no_update
from dash.exceptions import PreventUpdate
import plotly.graph_objects as go


# =========================
# Paths (relative to /code)
# =========================
CODE_DIR = Path(__file__).resolve().parent           # ...\GitHub\code
REPO_DIR = CODE_DIR.parent                           # ...\GitHub
DATA_DIR = REPO_DIR / "data"


def log(*a):
    print("[alps-dashboard]", *a, file=sys.stdout, flush=True)


GLACIERS_CSV = DATA_DIR / "glacier_location_and_name" / "glaciers_region11_alps.csv"
NC_DIR = Path(os.environ.get("GLACIER_NC_DIR", DATA_DIR / "glacier_model_data"))

METRICS_TABLE_PATH = DATA_DIR / "metrics_over_time_graphic" / "glacier_yearly_metrics.csv"
METRICS_DF = None
if METRICS_TABLE_PATH.exists():
    try:
        METRICS_DF = pd.read_csv(METRICS_TABLE_PATH)
        # normalize types
        if "year" in METRICS_DF.columns:
            METRICS_DF["year"] = pd.to_numeric(METRICS_DF["year"], errors="coerce")
        for c in ["rgi_id", "source", "experiment"]:
            if c in METRICS_DF.columns:
                METRICS_DF[c] = METRICS_DF[c].astype(str)
        log("Metrics rows loaded:", len(METRICS_DF))
    except Exception as e:
        METRICS_DF = None
        log("[WARN] Failed to load metrics table:", METRICS_TABLE_PATH, "::", repr(e))
else:
    log("[WARN] Metrics table not found:", METRICS_TABLE_PATH)



# =========================
# Config
# =========================
FONT_FAMILY = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

# Figure colours per UI theme (page colours are in assets/style.css)
THEMES = {
    "dark": {
        "fg": "#e6e6e6", "grid": "#262626",
        "map_style": "carto-darkmatter",
        "hillshade": "https://services.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade_Dark/MapServer/tile/{z}/{y}/{x}",
        "hillshade_opacity": 0.75,
        "marker": "#7dd3fc",
        "rcp": {"rcp_2_6": "#e3b505", "rcp_4_5": "#ff8c00", "rcp_8_5": "#ff6b6b"},
        "hist": "#a3a3a3",
    },
    "light": {
        "fg": "#1a1a1a", "grid": "#e8e8e8",
        "map_style": "carto-positron",
        "hillshade": "https://services.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}",
        "hillshade_opacity": 0.45,
        "marker": "#0369a1",
        "rcp": {"rcp_2_6": "#b58900", "rcp_4_5": "#e06c00", "rcp_8_5": "#d62828"},
        "hist": "#737373",
    },
}


def theme_of(name):
    return THEMES.get(name, THEMES["dark"])


def base_layout(theme, **extra):
    """Transparent figure background so the page colour shows through."""
    t = theme_of(theme)
    layout = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=t["fg"], family=FONT_FAMILY, size=12),
        margin=dict(l=0, r=0, t=0, b=0),
    )
    layout.update(extra)
    return layout

MAX_SIDE = 220  # 3D downsample

PROP_TO_VAR = {
    "Thickness (m)": "thk",
    "Velocity (m/a)": "velsurf_mag",
    "Surface Mass Balance (m/a)": "smb",
    "Mean Temperature (°C)": "mean_temp",
}

SCENARIO_LABELS = {
    "rcp_2_6": "RCP 2.6",
    "rcp_4_5": "RCP 4.5",
    "rcp_8_5": "RCP 8.5",
}

SCENARIO_COLORS = {
    "rcp_2_6": "#ffffff",
    "rcp_4_5": "#fbbf24",
    "rcp_8_5": "#fca5a5",
}

RGI_RE = re.compile(r"^(RGI2000-v7\.0-G-\d{2}-\d{5})_")
RCP_RE = re.compile(r"_(rcp_\d_\d)_", re.IGNORECASE)


def as_int(x) -> int:
    """Coerce dash/numpy scalars to plain Python int."""
    try:
        import numpy as _np
        a = _np.asarray(x)
        if a.ndim == 0:
            return int(a.item())
        # if list/array, take first element
        return int(a.reshape(-1)[0].item())
    except Exception:
        return int(x)





# =========================
# Load glaciers CSV
# =========================
def load_glaciers_csv(path: Path) -> pd.DataFrame:
    # robust: handles comma/semicolon, BOM, whitespace
    df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = (
        df.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    return df

def get_rgi_col(df: pd.DataFrame) -> str:
    if "rgi_id" in df.columns:
        return "rgi_id"

    norm = {
        c: str(c).strip().lower().replace("\ufeff", "").replace(" ", "").replace("-", "").replace("_", "")
        for c in df.columns
    }
    for c, n in norm.items():
        if n in ("rgiid", "rgi"):
            return c
        if "rgi" in n and "id" in n:
            return c

    raise KeyError(f"No RGI id column found. Columns: {list(df.columns)}")

def discover_nc_files(nc_dir: Path) -> dict:
    if not nc_dir.is_dir():
        raise FileNotFoundError(f"Missing NetCDF directory: {nc_dir}")

    buckets: dict[str, dict] = {}
    files = sorted(nc_dir.rglob("*.nc"))

    for p in files:
        name = p.name

        m = RGI_RE.match(name)
        if not m:
            continue
        rgi = m.group(1)

        entry = buckets.setdefault(
            rgi,
            {
                "chains": {"rcp_2_6": [], "rcp_4_5": [], "rcp_8_5": []},
                "w5e5_mean": None,
                "w5e5_const": None,
                "cordex_2d": None,
            },
        )

        # Past (annual) file
        if name.endswith("_Projection_output_W5E5_const.nc"):
            entry["w5e5_const"] = str(p)
            continue

        # Historical W5E5 mean file (baseline snapshots)
        if name.endswith("_Projection_output_W5E5_mean.nc"):
            entry["w5e5_mean"] = str(p)
            continue

        # CORDEX combined 2D file (2020–2100, 3 scenarios as a dim index 0/1/2)
        if name.endswith("_Projection_CORDEX_output_2D.nc"):
            entry["cordex_2d"] = str(p)
            continue

        # scenario chains (legacy fallback)
        mrcp = RCP_RE.search(name)
        if mrcp:
            sc = mrcp.group(1).lower()
            if sc in entry["chains"]:
                entry["chains"][sc].append(str(p))

    # stable sort
    for rgi, e in buckets.items():
        for sc in e["chains"]:
            e["chains"][sc] = sorted(e["chains"][sc])

    return buckets


# =========================
# NetCDF helpers (3D)
# =========================
AVAILABLE_ENGINES = set()
for eng in ("netcdf4", "h5netcdf"):
    try:
        __import__(eng)
        AVAILABLE_ENGINES.add(eng)
    except Exception:
        pass


# ---- Optional Dask + dataset cache (for smooth timelapse) ----
try:
    import dask  # noqa: F401
    _HAS_DASK = True
except Exception:
    _HAS_DASK = False

_DS_CACHE: dict[str, xr.Dataset] = {}

# The netCDF4/HDF5 C library is not thread-safe; serialize all file access
# when gunicorn runs this app with multiple threads per worker.
_NC_LOCK = threading.RLock()

def open_nc_cached(path: str) -> xr.Dataset:
    """Open NetCDF once and keep it cached (optionally chunked on time)."""
    ds = _DS_CACHE.get(path)
    if ds is not None:
        return ds

    # Prefer engines if available
    for eng in ("netcdf4", "h5netcdf"):
        if eng in AVAILABLE_ENGINES:
            try:
                if _HAS_DASK:
                    ds = xr.open_dataset(path, engine=eng, chunks={"time": 1})
                else:
                    ds = xr.open_dataset(path, engine=eng)
                _DS_CACHE[path] = ds
                return ds
            except Exception:
                pass

    # Fallback
    if _HAS_DASK:
        ds = xr.open_dataset(path, chunks={"time": 1})
    else:
        ds = xr.open_dataset(path)
    _DS_CACHE[path] = ds
    return ds

def years_array_from_time(tvals):
    tv = np.array(tvals)
    if np.issubdtype(tv.dtype, np.integer) or np.issubdtype(tv.dtype, np.floating):
        return tv.astype(int)
    try:
        return np.array([np.datetime64(x, "Y").astype(int) + 1970 for x in tv], dtype=int)
    except Exception:
        return np.array([int(str(x)[:4]) for x in tv], dtype=int)


def nearest_idx(years, target):
    return int(np.nanargmin(np.abs(np.asarray(years) - target)))


def coords_from_ds(ds, Z, sc_dim=None, sc_idx=None):
    """
    Return 1D x/y coordinate vectors matching Z (ny,nx).

    Handles CORDEX 2D where x/y are stored as (experiment, x)/(experiment, y).
    If sc_dim/sc_idx are provided, selects matching slice.
    """
    if "x" in ds.coords and "y" in ds.coords:
        xda = ds["x"]
        yda = ds["y"]

        if sc_dim is not None and sc_idx is not None:
            if sc_dim in xda.dims:
                xda = xda.isel({sc_dim: sc_idx})
            if sc_dim in yda.dims:
                yda = yda.isel({sc_dim: sc_idx})

        xs = np.asarray(xda.values, dtype=float).squeeze()
        ys = np.asarray(yda.values, dtype=float).squeeze()

        if xs.ndim != 1:
            xs = xs.reshape(-1)
        if ys.ndim != 1:
            ys = ys.reshape(-1)
        return xs, ys

    # fallback: synthetic coords
    ny, nx = Z.shape
    xs = np.arange(nx, dtype=float)
    ys = np.arange(ny, dtype=float)
    return xs, ys



def downsample(Z, xs, ys, max_side=MAX_SIDE):
    ny, nx = Z.shape
    sy = max(1, int(np.ceil(ny / max_side)))
    sx = max(1, int(np.ceil(nx / max_side)))
    if sy == 1 and sx == 1:
        return Z, xs, ys
    return Z[::sy, ::sx], xs[::sx], ys[::sy]


def get_var_or_fallback(ds, varname, indexer):
    def _arr(v):
        da = ds[v]
        idx = {k: indexer[k] for k in indexer if k in da.dims}
        return da.isel(**idx).values

    if varname in ds:
        arr = _arr(varname)
        rng = np.nanmax(arr) - np.nanmin(arr) if np.isfinite(arr).any() else 0.0
        if rng > 0:
            return arr

    if varname == "thk":
        us = _arr("usurf") if "usurf" in ds else None
        tg = ds["topg"].isel(time=indexer["time"]).values if ("topg" in ds and "time" in ds["topg"].dims) else (ds["topg"].values if "topg" in ds else None)
        if us is not None and tg is not None:
            return us - tg

    if varname == "velsurf_mag":
        if "velbar_mag" in ds:
            arr = _arr("velbar_mag")
            rng = np.nanmax(arr) - np.nanmin(arr) if np.isfinite(arr).any() else 0.0
            if rng > 0:
                return arr
        if {"velsurf_u","velsurf_v"}.issubset(ds.variables):
            u = _arr("velsurf_u")
            v = _arr("velsurf_v")
            arr = np.hypot(u, v)
            rng = np.nanmax(arr) - np.nanmin(arr) if np.isfinite(arr).any() else 0.0
            if rng > 0:
                return arr

    # safe fallback
    if "usurf" in ds:
        return np.zeros(ds["usurf"].isel(**indexer).shape, dtype=float)
    return np.zeros((10, 10), dtype=float)



# =========================
# CORDEX 2D helpers (scenario-in-file)
# =========================
SCENARIO_TO_IDX = {"rcp_2_6": 0, "rcp_4_5": 1, "rcp_8_5": 2}

def detect_scenario_dim(ds, preferred_var="usurf"):
    """Detect the scenario dimension (length-3) that is not time/x/y."""
    ref = preferred_var if preferred_var in ds.variables else None
    if ref is None:
        ref = next(iter(ds.data_vars), None)
    if ref is None:
        return None
    da = ds[ref]
    for d in da.dims:
        if d not in ("time", "x", "y") and (ds.sizes.get(d, None) == 3):
            return d
    return None

def load_slice_cordex2d(path, var, target_year, scenario_key):
    """
    Load CORDEX 2D projection data for a given scenario + year.

    This variant correctly handles time/x/y stored as (experiment, ...).
    Returns: topg, usurf, prop_map, thk, xs, ys, ysel
    """
    ds = open_nc_cached(path)
    sc_dim = "experiment" if ds.sizes.get("experiment", None) == 3 else detect_scenario_dim(ds, preferred_var=("usurf" if "usurf" in ds else var))
    if sc_dim is None:
        raise KeyError("Could not detect scenario dimension (expected a length-3 dim).")
    si = SCENARIO_TO_IDX.get(str(scenario_key), 1)

    # time axis can be 2D (experiment,time)
    if "time" not in ds.coords:
        raise KeyError("Dataset missing 'time' coordinate")
    if sc_dim in ds["time"].dims:
        years = years_array_from_time(ds["time"].isel({sc_dim: si}).values)
    else:
        years = years_array_from_time(ds["time"].values)

    ti = nearest_idx(years, as_int(target_year))

    def _isel_da(da):
        sel = {}
        if sc_dim in da.dims:
            sel[sc_dim] = si
        if "time" in da.dims:
            sel["time"] = ti
        return da.isel(**sel).values

    if "topg" not in ds.variables or "usurf" not in ds.variables:
        raise KeyError("Dataset missing required vars: topg/usurf")

    topg = _isel_da(ds["topg"])
    usurf = _isel_da(ds["usurf"])

    # property map
    if var in ds.variables:
        prop_map = _isel_da(ds[var])
    else:
        prop_map = np.full_like(usurf, np.nan)

    # thickness
    if "thk" in ds.variables:
        thk = _isel_da(ds["thk"])
    else:
        thk = usurf - topg

    xs, ys = coords_from_ds(ds, usurf, sc_dim=sc_dim, sc_idx=si)
    ysel = as_int(years[ti])
    return topg, usurf, prop_map, thk, xs, ys, ysel

def load_slice_chain(path, var, target_year):
    ds = open_nc_cached(path)
    years = years_array_from_time(ds["time"].values)
    ti = nearest_idx(years, as_int(target_year))

    if "topg" not in ds or "usurf" not in ds:
        raise KeyError("Dataset missing 'topg'/'usurf'")

    topg = ds["topg"].isel(time=0).values if ("time" in ds["topg"].dims) else ds["topg"].values
    usurf = ds["usurf"].isel(time=ti).values
    Zvar  = get_var_or_fallback(ds, var, {"time": ti})

    if "thk" in ds:
        thk = ds["thk"].isel(time=ti).values
    else:
        tg_ti = ds["topg"].isel(time=ti).values if ("time" in ds["topg"].dims) else topg
        thk = usurf - tg_ti

    xs, ys = coords_from_ds(ds, usurf)
    return topg, usurf, Zvar, thk, xs, ys, as_int(years[ti])


def choose_file_and_load(entry, scenario, var, target_year):
    """
    Data selection:
      - For years >= 2020: use single CORDEX 2D file (scenario dim index 0/1/2).
      - For years < 2020: use W5E5 const (annual 2000–2020) if available.
      - Fallback: W5E5 mean for 2000/2010 if present.
      - Legacy: first scenario chain file.
    """
    ty = int(target_year)

    if ty >= 2020 and entry.get("cordex_2d"):
        path = entry["cordex_2d"]
        return load_slice_cordex2d(path, var, ty, scenario), path

    if ty < 2020 and entry.get("w5e5_const"):
        path = entry["w5e5_const"]
        return load_slice_chain(path, var, ty), path

    if ty in (2000, 2010) and entry.get("w5e5_mean"):
        path = entry["w5e5_mean"]
        return load_slice_chain(path, var, ty), path

    chains = sorted(entry.get("chains", {}).get(scenario, []))
    if not chains:
        raise FileNotFoundError(f"No chain files for {scenario} (and no W5E5_const/CORDEX 2D available for year {ty})")
    return load_slice_chain(chains[0], var, ty), chains[0]


def default_scenario_for(rgi, data_index):
    e = data_index.get(rgi, {})
    if e.get("cordex_2d"):
        return "rcp_4_5"
    for sc in ("rcp_2_6", "rcp_4_5", "rcp_8_5"):
        if e.get("chains", {}).get(sc):
            return sc
    return "rcp_4_5"


# =========================
# Build data registries
# =========================
GLACIERS_DF = load_glaciers_csv(GLACIERS_CSV)
DATA_INDEX  = discover_nc_files(NC_DIR)

if not DATA_INDEX:
    raise RuntimeError(f"No NetCDF files found in: {NC_DIR}")

# Filter glaciers to only those that actually have NC data
rgi_col = get_rgi_col(GLACIERS_DF)
log("CSV columns:", [repr(c) for c in GLACIERS_DF.columns])
log("Using RGI column:", repr(rgi_col))
GLACIERS_DF = GLACIERS_DF[GLACIERS_DF[rgi_col].isin(DATA_INDEX.keys())].copy()
if GLACIERS_DF.empty:
    raise RuntimeError("No glaciers from glaciers_region11_alps.csv have matching NC files in data/glacier_model_data")

COUNTRIES = sorted(GLACIERS_DF["country"].unique().tolist())
ALL_RGIS  = sorted(GLACIERS_DF["rgi_id"].unique().tolist())
# Glacier shown on page load: the first of these that has model data
PREFERRED_DEFAULT_RGIS = [
    "RGI2000-v7.0-G-11-02596",  # Grosser Aletschgletscher (no model data yet, see glacierdash/ADD_ids.txt)
    "RGI2000-v7.0-G-11-01522",  # Mittelaletschgletscher
]
DEFAULT_RGI = next((r for r in PREFERRED_DEFAULT_RGIS if r in ALL_RGIS), ALL_RGIS[0])
DEFAULT_COUNTRY = GLACIERS_DF.loc[GLACIERS_DF["rgi_id"] == DEFAULT_RGI, "country"].iloc[0]
GLACIER_NAMES = {
    str(r): str(n).strip()
    for r, n in zip(GLACIERS_DF["rgi_id"], GLACIERS_DF["glac_name"].fillna(""))
    if str(n).strip() and str(n).strip().lower() != "nan"
}


# Model years (annual steps)
YEARS = list(range(2000, 2101))

DEFAULT_YEAR = 2020



VAR_TO_PROP = {v: k for k, v in PROP_TO_VAR.items()}
DEFAULT_VAR = "thk"

METRIC_LABELS = {
    "volume_km3": "Volume (km³)",
    "area_km2": "Area (km²)",
    "thk_mean_m": "Mean thickness (m)",
    "smb_mean": "Mean SMB (m/a)",
    "vel_mean": "Mean velocity (m/a)",
}
# area_km2 is in the table too, but it is constant over time (grid area), so it is not offered
METRIC_OPTIONS = ["volume_km3", "thk_mean_m", "smb_mean", "vel_mean"]
DEFAULT_METRIC = "volume_km3"


def glacier_label(rgi):
    name = GLACIER_NAMES.get(str(rgi))
    return f"{name} ({rgi})" if name else str(rgi)


def glacier_options(country):
    dff = GLACIERS_DF if not country else GLACIERS_DF[GLACIERS_DF["country"] == country]
    return [{"label": glacier_label(r), "value": r} for r in dff["rgi_id"]]


def country_of(rgi):
    row = GLACIERS_DF.loc[GLACIERS_DF["rgi_id"] == rgi, "country"]
    return row.iloc[0] if len(row) else None


def url_params(search):
    """Query string -> {key: first value}."""
    return {k: v[0] for k, v in parse_qs((search or "").lstrip("?")).items() if v}


# =========================
# Figures
# =========================
def fit_map_view(lats, lons, width, height, max_zoom=9.0):
    """Centre and zoom so that all points fit a width×height px web-mercator map."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    lat0, lat1 = float(np.nanmin(lats)), float(np.nanmax(lats))
    lon0, lon1 = float(np.nanmin(lons)), float(np.nanmax(lons))

    def merc(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    pad = 1.3  # leave a margin around the outermost glaciers
    frac_x = max(lon1 - lon0, 0.02) * pad / 360.0
    frac_y = max(merc(lat1) - merc(lat0), 0.0005) * pad / (2 * math.pi)
    # MapLibre renders 512 px tiles: world width in px = 512 * 2**zoom
    zoom = min(math.log2(max(width, 100) / 512 / frac_x), math.log2(max(height, 100) / 512 / frac_y), max_zoom)
    ymid = (merc(lat0) + merc(lat1)) / 2
    lat_c = math.degrees(2 * math.atan(math.exp(ymid)) - math.pi / 2)
    return {"lat": lat_c, "lon": (lon0 + lon1) / 2}, zoom


def make_globe_fig(dff: pd.DataFrame, selected_rgi, theme, center, zoom, uirevision):
    """Overview map: glacier markers on a hillshade basemap."""
    t = theme_of(theme)
    fig = go.Figure()

    fig.add_trace(go.Scattermap(
        lon=dff["cenlon"],
        lat=dff["cenlat"],
        mode="markers",
        marker=dict(size=8, color=t["marker"]),
        text=[glacier_label(r) for r in dff["rgi_id"]],
        customdata=dff["rgi_id"],
        hovertemplate="%{text}<extra></extra>",
    ))

    sel = GLACIERS_DF[GLACIERS_DF["rgi_id"] == selected_rgi]
    if not sel.empty:
        s = sel.iloc[0]
        fig.add_trace(go.Scattermap(
            lon=[float(s.cenlon)],
            lat=[float(s.cenlat)],
            mode="markers",
            marker=dict(size=14, color="#f97316"),
            text=[glacier_label(s.rgi_id)],
            customdata=[s.rgi_id],
            hovertemplate="Selected: %{text}<extra></extra>",
        ))

    fig.update_layout(**base_layout(
        theme,
        showlegend=False,
        uirevision=uirevision,  # a new value re-applies centre/zoom; otherwise the user's pan/zoom is kept
        map=dict(
            style=t["map_style"],
            center=center,
            zoom=zoom,
            layers=[dict(
                sourcetype="raster",
                source=[t["hillshade"]],
                below="traces",
                opacity=t["hillshade_opacity"],
            )],
        ),
    ))
    return fig


def typed_array(a):
    """numpy array -> plotly.js typed-array spec (float32, base64); about 4x smaller than JSON numbers."""
    a = np.ascontiguousarray(a, dtype=np.float32)
    return {"dtype": "f4", "bdata": base64.b64encode(a.tobytes()).decode("ascii"), "shape": ",".join(map(str, a.shape))}


# =========================
# App
# =========================
# Styling lives in assets/style.css. The theme is a data-theme attribute on <html>;
# this inline script applies the saved theme before first paint so there is no flash.
INDEX_STRING = """<!DOCTYPE html>
<html data-theme="dark">
<head>
{%metas%}
<title>{%title%}</title>
<script>try{var t=JSON.parse(localStorage.getItem("theme"));if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;}catch(e){}</script>
{%favicon%}
{%css%}
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>"""

app = Dash(
    __name__,
    assets_folder=str(CODE_DIR / "assets"),
    title="Glacier Dashboard — Alps (RGI 11)",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    index_string=INDEX_STRING,
)
server = app.server  # WSGI entry point for gunicorn

LOGO_FAU = "Friedrich-Alexander-Universität_Erlangen-Nürnberg_Logo_07.2022.svg.png"
LOGO_ERC = "LOGO_ERC-FLAG_EU-no text.png"


def panel_head(title, *extra):
    return html.Div(className="panel-head", children=[html.H2(title), *extra])


def field(label, control):
    return html.Label(className="field", children=[html.Span(label), control])


def logos(class_name):
    # rendered twice: in the sidebar (landscape) and as a footer (portrait); CSS shows one
    return html.Div(className=class_name, children=[
        html.Img(src=app.get_asset_url(LOGO_FAU), className="logo-fau",
                 alt="Friedrich-Alexander-Universität Erlangen-Nürnberg"),
        html.Img(src=app.get_asset_url(LOGO_ERC), className="logo-erc",
                 alt="Funded by the European Union · European Research Council"),
    ])


def empty_fig(theme="dark"):
    return go.Figure(layout=base_layout(theme, xaxis={"visible": False}, yaxis={"visible": False}))


app.layout = html.Div(
    className="app",
    children=[
        # URL (?glacier=…&scenario=…&property=…&year=…&metric=…) is read once on load
        # and kept up to date with history.replaceState, so any view can be bookmarked or shared.
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="url_sync"),
        dcc.Store(id="selected_rgi"),
        dcc.Store(id="map_view"),          # {"bounds"|"point", "rev"}: where the map should move to
        dcc.Store(id="globe_size"),        # map size in px, measured in the browser
        dcc.Store(id="surface_static"),    # signature of what the 3D figure shows apart from the ice surface
        dcc.Store(id="frame_year"),        # last year the 3D callback finished (paces the timelapse)
        dcc.Store(id="camera_mode", data="free"),
        dcc.Store(id="theme", data="dark", storage_type="local"),
        dcc.Interval(id="timelapse_interval", interval=300, disabled=True),

        # Controls
        html.Section(
            className="panel panel-ctrl",
            children=[
                html.Div(
                    className="brand",
                    children=[
                        html.Div([
                            html.H1("Alpine glacier evolution"),
                            html.P("RGI region 11 · 2000–2100", className="muted"),
                        ]),
                        html.Button("☀", id="theme_toggle", n_clicks=0, className="btn icon-btn",
                                    title="Switch light/dark theme", **{"aria-label": "Switch light/dark theme"}),
                    ],
                ),
                html.Div(
                    className="fields",
                    children=[
                        field("Country", dcc.Dropdown(
                            id="country_select",
                            options=[{"label": c, "value": c} for c in COUNTRIES],
                            clearable=True,
                            placeholder="All countries",
                        )),
                        field("Glacier", dcc.Dropdown(
                            id="rgi_select",
                            options=[],  # filled by callback
                            clearable=False,
                            placeholder="Search by name or RGI ID",
                        )),
                        field("Scenario", dcc.Dropdown(
                            id="scenario",
                            options=[{"label": SCENARIO_LABELS[k], "value": k} for k in SCENARIO_LABELS],
                            clearable=False,
                            searchable=False,
                        )),
                        field("Property", dcc.Dropdown(
                            id="property",
                            options=[{"label": k, "value": v} for k, v in PROP_TO_VAR.items()],
                            clearable=False,
                            searchable=False,
                        )),
                    ],
                ),
                logos("logos logos-side"),
            ],
        ),

        # 3D view
        html.Section(
            className="panel panel-3d",
            children=[
                panel_head("Glacier view", html.Span(id="surface_note", className="muted ellipsis")),
                html.Div(className="plot", children=dcc.Loading(
                    parent_className="plot-fill",
                    type="circle",
                    color="#0ea5e9",
                    delay_show=700,  # timelapse frames are faster than this, so no flicker
                    overlay_style={"visibility": "visible", "opacity": 0.6},
                    children=dcc.Graph(
                        id="mnt_surface",
                        className="graph",
                        figure=empty_fig(),
                        responsive=True,
                        config={"displayModeBar": False},
                    ),
                )),
                html.Div(
                    className="toolbar",
                    children=[
                        html.Button("▶ Play", id="btn_timelapse", n_clicks=0, className="btn"),
                        html.Button("2D map", id="btn_topdown", n_clicks=0, className="btn",
                                    title="Switch between the 3D view and a top-down map"),
                        dcc.Checklist(
                            id="toggle_isohypses",
                            className="check",
                            options=[{"label": "200 m contours", "value": "iso"}],
                            value=[],
                        ),
                        html.Div(
                            className="year",
                            children=dcc.Slider(
                                id="year_slider",
                                min=min(YEARS),
                                max=max(YEARS),
                                step=1,
                                value=DEFAULT_YEAR,
                                marks={y: str(y) for y in range(min(YEARS), max(YEARS) + 1, 20)},
                                tooltip={"placement": "top", "always_visible": False},
                            ),
                        ),
                    ],
                ),
            ],
        ),

        # Overview map
        html.Section(
            className="panel panel-map",
            children=[
                panel_head("Overview map"),
                html.Div(className="plot", children=dcc.Graph(
                    id="globe",
                    className="graph",
                    figure=empty_fig(),
                    responsive=True,
                    config={"displayModeBar": False, "scrollZoom": True},
                )),
            ],
        ),

        # Time series
        html.Section(
            className="panel panel-ts",
            children=[
                panel_head(
                    "Projection to 2100",
                    dcc.Dropdown(
                        id="metric_var_select",
                        className="head-select",
                        options=[{"label": METRIC_LABELS[m], "value": m} for m in METRIC_OPTIONS],
                        clearable=False,
                        searchable=False,
                    ),
                ),
                html.Div(className="ts-legend", children=[
                    html.Span("Historical", className="lg lg-hist"),
                    *[html.Span(label, className=f"lg lg-{key}") for key, label in SCENARIO_LABELS.items()],
                ]),
                html.Div(className="plot", children=dcc.Graph(
                    id="glacier_timeseries",
                    className="graph",
                    figure=empty_fig(),
                    responsive=True,
                    config={"displayModeBar": False},
                )),
            ],
        ),

        logos("logos logos-foot"),
    ],
)


# =========================
# Client-side callbacks: theme, URL, map size
# =========================
app.clientside_callback(
    "function(n, t) { return t === 'light' ? 'dark' : 'light'; }",
    Output("theme", "data"),
    Input("theme_toggle", "n_clicks"),
    State("theme", "data"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """function(t) {
        t = (t === 'light') ? 'light' : 'dark';
        document.documentElement.dataset.theme = t;
        return t === 'dark' ? '☀' : '☾';
    }""",
    Output("theme_toggle", "children"),
    Input("theme", "data"),
)

app.clientside_callback(
    """function(glacier, scenario, property, year, metric) {
        const p = new URLSearchParams();
        if (glacier) p.set('glacier', glacier);
        if (scenario) p.set('scenario', scenario);
        if (property) p.set('property', property);
        if (year) p.set('year', year);
        if (metric) p.set('metric', metric);
        window.history.replaceState(window.history.state, '', window.location.pathname + '?' + p.toString());
        return window.dash_clientside.no_update;
    }""",
    Output("url_sync", "data"),
    Input("selected_rgi", "data"),
    Input("scenario", "value"),
    Input("property", "value"),
    Input("year_slider", "value"),
    Input("metric_var_select", "value"),
    prevent_initial_call=True,
)

app.clientside_callback(
    """function(_) {
        const el = document.getElementById('globe');
        return el ? {w: el.clientWidth, h: el.clientHeight} : {w: 600, h: 400};
    }""",
    Output("globe_size", "data"),
    Input("url", "pathname"),
)


# =========================
# Callbacks: selection and settings
# =========================
@app.callback(
    Output("scenario", "value"),
    Output("property", "value"),
    Output("metric_var_select", "value"),
    Input("url", "search"),
)
def settings_from_url(search):
    q = url_params(search)
    scenario = q.get("scenario") if q.get("scenario") in SCENARIO_LABELS else "rcp_4_5"
    var = q.get("property") if q.get("property") in VAR_TO_PROP else DEFAULT_VAR
    metric = q.get("metric") if q.get("metric") in METRIC_OPTIONS else DEFAULT_METRIC
    return scenario, var, metric


@app.callback(
    Output("selected_rgi", "data"),
    Output("country_select", "value"),
    Output("rgi_select", "options"),
    Output("rgi_select", "value"),
    Output("map_view", "data"),
    Input("url", "search"),
    Input("country_select", "value"),
    Input("globe", "clickData"),
    Input("rgi_select", "value"),
    State("selected_rgi", "data"),
    State("map_view", "data"),
)
def select_glacier(search, country, click, rgi_dropdown, current_rgi, view):
    """Single owner of the glacier selection (map click, dropdown, country filter, URL)."""
    trig = ctx.triggered_id
    rev = (view or {}).get("rev", 0) + 1

    if trig == "globe":
        rgi = ((click or {}).get("points") or [{}])[0].get("customdata")
        if not rgi or rgi == current_rgi:
            raise PreventUpdate
        # keep the country filter and the map view where the user put them
        return rgi, country, no_update, rgi, no_update

    if trig == "rgi_select":
        if not rgi_dropdown or rgi_dropdown == current_rgi:
            raise PreventUpdate
        return rgi_dropdown, country, no_update, rgi_dropdown, {"point": rgi_dropdown, "rev": rev}

    if trig == "country_select":
        in_country = GLACIERS_DF if not country else GLACIERS_DF[GLACIERS_DF["country"] == country]
        rgi = current_rgi if current_rgi in set(in_country["rgi_id"]) else in_country["rgi_id"].iloc[0]
        return rgi, country, glacier_options(country), rgi, {"bounds": country, "rev": rev}

    # initial load: glacier from the URL, else the default
    rgi = url_params(search).get("glacier")
    if rgi not in ALL_RGIS:
        rgi = DEFAULT_RGI
    country = country_of(rgi)
    return rgi, country, glacier_options(country), rgi, {"bounds": country, "rev": rev}


@app.callback(
    Output("globe", "figure"),
    Input("country_select", "value"),
    Input("selected_rgi", "data"),
    Input("theme", "data"),
    Input("map_view", "data"),
    Input("globe_size", "data"),
)
def update_globe(country, selected_rgi, theme, view, size):
    dff = GLACIERS_DF if not country else GLACIERS_DF[GLACIERS_DF["country"] == country]
    view = view or {}
    size = size or {"w": 600, "h": 400}
    w, h = size.get("w") or 600, size.get("h") or 400

    point = GLACIERS_DF[GLACIERS_DF["rgi_id"] == view.get("point")]
    if not point.empty:
        # glacier picked from the list: pan to it, zoomed in enough to find it
        center, zoom = {"lat": float(point.cenlat.iloc[0]), "lon": float(point.cenlon.iloc[0])}, 8.0
    else:
        bounds = GLACIERS_DF if not view.get("bounds") else GLACIERS_DF[GLACIERS_DF["country"] == view["bounds"]]
        center, zoom = fit_map_view(bounds["cenlat"], bounds["cenlon"], w, h)

    return make_globe_fig(dff, selected_rgi, theme, center, zoom, uirevision=f"{view.get('rev', 0)}|{w}x{h}")


# =========================
# 3D UI controls (Top-down + Timelapse + year)
# =========================
@app.callback(
    Output("camera_mode", "data"),
    Output("btn_topdown", "children"),
    Input("btn_topdown", "n_clicks"),
    State("camera_mode", "data"),
    prevent_initial_call=True,
)
def toggle_topdown(n, mode):
    mode = "free" if mode == "topdown" else "topdown"
    return mode, ("3D view" if mode == "topdown" else "2D map")


@app.callback(
    Output("timelapse_interval", "disabled"),
    Output("btn_timelapse", "children"),
    Input("btn_timelapse", "n_clicks"),
    State("timelapse_interval", "disabled"),
    prevent_initial_call=True,
)
def toggle_timelapse(n, disabled):
    playing = bool(disabled)  # it was stopped, so start it
    return (not playing), ("⏸ Pause" if playing else "▶ Play")


@app.callback(
    Output("year_slider", "value"),
    Input("url", "search"),
    Input("timelapse_interval", "n_intervals"),
    Input("glacier_timeseries", "clickData"),
    State("year_slider", "value"),
    State("frame_year", "data"),
)
def set_year(search, n_intervals, ts_click, current_year, frame_year):
    trig = ctx.triggered_id

    if trig == "timelapse_interval":
        # only step once the previous frame has been drawn, so slow frames don't pile up
        if frame_year is not None and frame_year != current_year:
            raise PreventUpdate
        i = YEARS.index(current_year) if current_year in YEARS else -1
        return YEARS[(i + 1) % len(YEARS)]

    if trig == "glacier_timeseries":
        try:
            x = float(ts_click["points"][0]["x"])
        except Exception:
            raise PreventUpdate
        return int(min(max(round(x), YEARS[0]), YEARS[-1]))

    try:
        year = int(url_params(search).get("year", DEFAULT_YEAR))
    except ValueError:
        year = DEFAULT_YEAR
    return year if year in YEARS else DEFAULT_YEAR


# =========================
# 3D data
# =========================
def load_icemask_slice(nc_path: str, target_year: int, scenario_key: str):
    """
    Return an ice mask slice for the given year/scenario.

    CORDEX 2D files often store time/x/y as (experiment, ...). This function
    slices those consistently.
    """
    try:
        ds = open_nc_cached(nc_path)
        if "icemask" not in ds.variables or "time" not in ds.coords:
            return None

        sc_dim = "experiment" if ds.sizes.get("experiment", None) == 3 else detect_scenario_dim(ds)
        si = SCENARIO_TO_IDX.get(str(scenario_key), 1)

        # time can be (experiment,time) -> slice experiment first
        if sc_dim and sc_dim in ds["time"].dims:
            years = years_array_from_time(ds["time"].isel({sc_dim: si}).values)
        else:
            years = years_array_from_time(ds["time"].values)

        ti = nearest_idx(years, int(target_year))

        da = ds["icemask"]
        sel = {}
        if "time" in da.dims:
            sel["time"] = ti
        if sc_dim and sc_dim in da.dims:
            sel[sc_dim] = si

        return da.isel(**sel).values
    except Exception:
        return None


# ---- Manual LRU cache for heavy 3D per-frame arrays ----
_CACHED_3D_FIELDS = OrderedDict()
# ~1 MB per entry at MAX_SIDE=220; 128 covers a full timelapse run for one glacier/scenario/property
_CACHED_3D_FIELDS_MAX = int(os.environ.get("GLACIER_3D_CACHE_SIZE", "128"))
_CACHED_3D_LOCK = threading.Lock()


def _frame_fields(rgi, scenario, var, target_year):
    """Downsampled, glacier-masked arrays for one frame (cached)."""
    key = (str(rgi), str(scenario), str(var), int(target_year))
    with _CACHED_3D_LOCK:
        if key in _CACHED_3D_FIELDS:
            _CACHED_3D_FIELDS.move_to_end(key)
            return _CACHED_3D_FIELDS[key]

    entry = DATA_INDEX.get(str(rgi), None)
    if not entry:
        raise KeyError(f"No files for {rgi}")

    with _NC_LOCK:
        (topg, usurf, prop_map, thk, xs, ys, ysel), source_path = choose_file_and_load(entry, str(scenario), str(var), int(target_year))
        icemask_full = load_icemask_slice(source_path, int(target_year), str(scenario))

    # downsample (CPU + WebGL payload)
    topg_ds, xs_ds, ys_ds = downsample(topg, xs, ys, MAX_SIDE)
    usurf_ds, _, _        = downsample(usurf, xs, ys, MAX_SIDE)
    prop_ds,  _, _        = downsample(prop_map, xs, ys, MAX_SIDE)
    thk_ds,   _, _        = downsample(thk, xs, ys, MAX_SIDE)

    # glacier mask: use *year-specific* thickness so vanished ice becomes holes
    eps = 0.5  # meters; increase if you want more aggressive "vanish" masking
    glacier_mask = np.isfinite(thk_ds) & (thk_ds > eps)
    if icemask_full is not None:
        icemask_ds, _, _ = downsample(icemask_full, xs, ys, MAX_SIDE)
        glacier_mask &= np.isfinite(icemask_ds) & (icemask_ds > 0.5)

    # center coords (stable WebGL); 1D axes are enough for go.Surface
    x_center = 0.5 * (float(xs_ds[0]) + float(xs_ds[-1]))
    y_center = 0.5 * (float(ys_ds[0]) + float(ys_ds[-1]))
    bedrock = np.where(np.isfinite(topg_ds), topg_ds, np.nan)

    res = {
        "xs": np.asarray(xs_ds - x_center),
        "ys": np.asarray(ys_ds - y_center),
        "bedrock": bedrock,
        # identifies the static part of the scene (grid + bedrock), see update_3d
        "bedrock_key": hashlib.sha1(np.ascontiguousarray(bedrock, dtype=np.float32).tobytes()).hexdigest()[:16],
        "top_z": np.where(glacier_mask, usurf_ds, np.nan),
        "prop": np.where(glacier_mask, prop_ds, np.nan),
        "source_file": os.path.basename(source_path),
        "year": int(ysel),
    }
    with _CACHED_3D_LOCK:
        _CACHED_3D_FIELDS[key] = res
        while len(_CACHED_3D_FIELDS) > _CACHED_3D_FIELDS_MAX:
            _CACHED_3D_FIELDS.popitem(last=False)
    return res


def _robust_limits(A, default_span=1.0):
    finite = np.isfinite(A)
    if not finite.any():
        return 0.0, default_span
    lo = float(np.nanpercentile(A[finite], 1))
    hi = float(np.nanpercentile(A[finite], 99))
    if (not np.isfinite(lo)) or (not np.isfinite(hi)) or (hi <= lo):
        lo = float(np.nanmin(A[finite]))
        hi = float(np.nanmax(A[finite]))
    if (not np.isfinite(lo)) or (not np.isfinite(hi)) or (hi <= lo):
        return 0.0, default_span
    return lo, hi


_COLOR_LIMITS: dict = {}
_COLOR_LIMITS_LOCK = threading.Lock()


def color_limits(rgi, var):
    """
    Colour range for a glacier + property, fixed over all years and scenarios
    so colours stay comparable during a timelapse. Taken from the first year
    and the last year of every scenario.
    """
    key = (str(rgi), str(var))
    with _COLOR_LIMITS_LOCK:
        if key in _COLOR_LIMITS:
            return _COLOR_LIMITS[key]

    samples = []
    frames = [("rcp_4_5", YEARS[0])] + [(sc, YEARS[-1]) for sc in SCENARIO_LABELS]
    for sc, year in frames:
        try:
            p = _frame_fields(rgi, sc, var, year)["prop"]
        except Exception:
            continue
        samples.append(p[np.isfinite(p)])
    values = np.concatenate(samples) if samples else np.array([])

    if var == "smb":
        limits = ("RdBu", -10.0, 10.0)
    elif var in ("velsurf_mag", "velsurf"):
        limits = ("Magma", *_robust_limits(values, default_span=1.0))
    elif var in ("mean_temp", "t2m", "temp"):
        lo, hi = _robust_limits(values, default_span=5.0)
        vmax = max(abs(lo), abs(hi), 1.0)
        limits = ("RdBu_r", -vmax, vmax)
    elif var == "thk":
        _, hi = _robust_limits(values, default_span=10.0)
        limits = ("Blues", 0.0, max(hi, 0.1))
    else:
        limits = ("Blues", *_robust_limits(values, default_span=10.0))

    with _COLOR_LIMITS_LOCK:
        _COLOR_LIMITS[key] = limits
    return limits


BEDROCK_CS = [[0.0, "#303030"], [0.2, "#505050"], [0.5, "#808080"], [0.8, "#b8b8b8"], [1.0, "#d8d8d8"]]
_SURF_Z_EPS = 2.0      # lift the ice surface slightly to avoid z-fighting with the bedrock
TOP_TRACE_INDEX = 6    # base, 4 walls, bedrock, ice surface
MAP_TRACE_INDEX = 1    # 2D map: hillshade, ice property, (contours)


def hillshade(z, xs, ys, azimuth=315.0, altitude=45.0):
    """Shaded relief (0..1) of an elevation grid, light from the north-west."""
    zf = np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0.0)
    dx = float(np.mean(np.diff(xs))) if len(xs) > 1 else 1.0
    dy = float(np.mean(np.diff(ys))) if len(ys) > 1 else 1.0
    dz_dy, dz_dx = np.gradient(zf, dy, dx)
    slope = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(-dz_dx, dz_dy)
    az, alt = math.radians(azimuth), math.radians(altitude)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shade, 0.0, 1.0)


def map2d_figure(f, prop_field, colorscale, cmin, cmax, property_label, show_iso, theme, rgi):
    """Top-down map: shaded bedrock with the glacier coloured by the selected property."""
    xs, ys, bedrock = f["xs"], f["ys"], f["bedrock"]
    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        x=xs, y=ys, z=hillshade(bedrock, xs, ys).astype(np.float32),
        # mid-grey relief, so the ice (whose colour scales start near white) stands out
        colorscale=[[0, "#1c1c1c"], [1, "#a8a8a8"]], zmin=0, zmax=1,
        showscale=False, hoverinfo="skip", name="Terrain",
    ))
    # MAP_TRACE_INDEX: the only trace a year change updates
    fig.add_trace(go.Heatmap(
        x=xs, y=ys, z=prop_field.astype(np.float32),
        colorscale=colorscale, zmin=cmin, zmax=cmax,
        colorbar=dict(title=dict(text=property_label, side="right"), len=0.75, thickness=12, outlinewidth=0),
        hovertemplate=f"{property_label}: %{{z:.1f}}<extra></extra>", name=property_label,
    ))
    if show_iso and np.isfinite(bedrock).any():
        fig.add_trace(go.Contour(
            x=xs, y=ys, z=bedrock.astype(np.float32),
            contours=dict(coloring="lines", start=math.floor(np.nanmin(bedrock) / 200) * 200,
                          end=math.ceil(np.nanmax(bedrock) / 200) * 200, size=200,
                          showlabels=True, labelfont=dict(size=10, color="#ffffff")),
            line=dict(width=1, color="rgba(255,255,255,0.6)"), colorscale=[[0, "#fff"], [1, "#fff"]],
            showscale=False, hoverinfo="skip", name="Contours",
        ))
    hidden = dict(visible=False, showgrid=False, zeroline=False)
    fig.update_layout(**base_layout(
        theme,
        uirevision=f"{rgi}|2d",
        xaxis=dict(hidden, constrain="domain"),
        yaxis=dict(hidden, scaleanchor="x", scaleratio=1, constrain="domain"),
    ))
    return fig


def _surface_arrays(f, var):
    """Ice-surface heights and colour values for a frame, plus how to colour them."""
    colorscale, cmin, cmax = color_limits(f["_rgi"], var)
    prop_field, note = f["prop"], ""
    if (not np.isfinite(prop_field).any()) or not (cmax > cmin):
        if np.isfinite(f["top_z"]).any():
            prop_field = f["top_z"]
            colorscale = "Viridis"
            cmin, cmax = _robust_limits(prop_field, default_span=1.0)
            note = " (showing elevation: property missing/flat)"
    top_z = np.where(np.isfinite(f["top_z"]), f["top_z"] + _SURF_Z_EPS, np.nan)
    return top_z, prop_field, colorscale, float(cmin), float(cmax), note


@app.callback(
    Output("mnt_surface", "figure"),
    Output("surface_note", "children"),
    Output("surface_note", "title"),
    Output("surface_static", "data"),
    Output("frame_year", "data"),
    Input("selected_rgi", "data"),
    Input("scenario", "value"),
    Input("property", "value"),
    Input("year_slider", "value"),
    Input("toggle_isohypses", "value"),
    Input("camera_mode", "data"),
    Input("theme", "data"),
    State("surface_static", "data"),
)
def update_3d(rgi, scenario, var, target_year, show_iso_values, camera_mode, theme, shown_static):
    if not rgi or var not in VAR_TO_PROP or not scenario:
        return empty_fig(theme), "Pick a glacier", "", None, target_year
    property_label = VAR_TO_PROP[var]

    try:
        f = dict(_frame_fields(rgi, scenario, var, target_year), _rgi=rgi)
        top_z, prop_field, colorscale, cmin, cmax, extra_note = _surface_arrays(f, var)
    except Exception as e:
        return empty_fig(theme), f"Error loading frame: {type(e).__name__}: {e}", repr(e), None, target_year

    show_iso = isinstance(show_iso_values, (list, tuple)) and ("iso" in show_iso_values)
    name = GLACIER_NAMES.get(str(rgi)) or str(rgi)
    note = f"{name} · {SCENARIO_LABELS.get(scenario, scenario)} · {property_label} · {f['year']}{extra_note}"
    hover = f"{rgi}\nSource file: {f['source_file']}"

    # Everything except the ice surface is the same from year to year. If the browser
    # already shows that, send only the new ice surface instead of the whole figure.
    static = "|".join(map(str, (rgi, scenario, var, f["bedrock_key"], show_iso, camera_mode, theme, colorscale, cmin, cmax)))
    if static == shown_static:
        patch = Patch()
        if camera_mode == "topdown":
            patch["data"][MAP_TRACE_INDEX]["z"] = typed_array(prop_field)
        else:
            patch["data"][TOP_TRACE_INDEX]["z"] = typed_array(top_z)
            patch["data"][TOP_TRACE_INDEX]["surfacecolor"] = typed_array(prop_field)
        return patch, note, hover, static, target_year

    if camera_mode == "topdown":
        fig = map2d_figure(f, prop_field, colorscale, cmin, cmax, property_label, show_iso, theme, rgi)
        return fig, note, hover, static, target_year

    xs_l, ys_l, bedrock = f["xs"], f["ys"], f["bedrock"]

    # isohypses on bedrock
    zmin_bed = float(np.nanmin(bedrock)) if np.isfinite(bedrock).any() else 0.0
    zmax_bed = float(np.nanmax(bedrock)) if np.isfinite(bedrock).any() else zmin_bed
    start    = (np.floor(zmin_bed/200.0)*200.0) if zmax_bed > zmin_bed else zmin_bed
    end      = (np.ceil( zmax_bed/200.0)*200.0) if zmax_bed > zmin_bed else zmax_bed
    contour_cfg_bed = dict(z=dict(show=show_iso, start=start, end=end, size=200, color="#ffffff", width=2))

    base_z = zmin_bed - max(50.0, 0.1 * (zmax_bed - zmin_bed))

    fig = go.Figure()

    x0, x1 = float(xs_l[0]), float(xs_l[-1])
    y0, y1 = float(ys_l[0]), float(ys_l[-1])

    # base plane (flat, so 2x2 corners are enough)
    Zbase = np.full((2, 2), base_z)
    fig.add_trace(go.Surface(
        x=[x0, x1], y=[y0, y1], z=Zbase,
        surfacecolor=Zbase, colorscale=BEDROCK_CS,
        cmin=zmin_bed, cmax=zmax_bed,
        showscale=False, opacity=1.0, name="Base", hoverinfo="skip",
    ))

    # walls
    def add_wall(x2d, y2d, z1d_edge):
        Z = np.vstack([np.full_like(z1d_edge, base_z), np.nan_to_num(z1d_edge, nan=base_z)])
        fig.add_trace(go.Surface(
            x=x2d, y=y2d, z=Z,
            surfacecolor=Z, colorscale=BEDROCK_CS,
            cmin=zmin_bed, cmax=zmax_bed,
            showscale=False, opacity=1.0, name="Wall", hoverinfo="skip",
        ))

    add_wall(np.vstack([np.full_like(ys_l, x0), np.full_like(ys_l, x0)]), np.vstack([ys_l, ys_l]), bedrock[:, 0])
    add_wall(np.vstack([np.full_like(ys_l, x1), np.full_like(ys_l, x1)]), np.vstack([ys_l, ys_l]), bedrock[:, -1])
    add_wall(np.vstack([xs_l, xs_l]), np.vstack([np.full_like(xs_l, y0), np.full_like(xs_l, y0)]), bedrock[0, :])
    add_wall(np.vstack([xs_l, xs_l]), np.vstack([np.full_like(xs_l, y1), np.full_like(xs_l, y1)]), bedrock[-1, :])

    # bedrock surface
    fig.add_trace(go.Surface(
        x=xs_l, y=ys_l, z=bedrock.astype(np.float32), colorscale=BEDROCK_CS,
        showscale=False, opacity=1.0, name="Bedrock",
        contours=contour_cfg_bed,
    ))

    # ice surface (TOP_TRACE_INDEX; the only trace a year change updates)
    fig.add_trace(go.Surface(
        x=xs_l, y=ys_l, z=top_z.astype(np.float32),
        surfacecolor=prop_field.astype(np.float32), colorscale=colorscale,
        cmin=cmin, cmax=cmax,
        colorbar=dict(title=dict(text=property_label, side="right"), len=0.75, thickness=12, outlinewidth=0),
        opacity=1.0, name=property_label,
    ))

    # edge labels for isohypses
    ann = []
    if show_iso and (end > start):
        left_vals = bedrock[:, 0] if bedrock.shape[1] > 0 else np.array([])
        if np.isfinite(left_vals).any():
            mask = np.isfinite(left_vals)
            for L in range(int(start), int(end) + 1, 200):
                dif = np.abs(left_vals[mask] - L)
                if dif.size == 0:
                    continue
                i_rel = int(np.argmin(dif))
                if dif[i_rel] <= 100:
                    i = int(np.arange(len(left_vals))[mask][i_rel])
                    ann.append(dict(
                        x=float(x0), y=float(ys_l[i]), z=float(left_vals[i]),
                        text=f"{int(L)} m", showarrow=False, xanchor="left", yanchor="middle",
                        font=dict(color="#ffffff", size=11), bgcolor="rgba(0,0,0,0.45)"
                    ))

        top_vals = bedrock[-1, :] if bedrock.shape[0] > 0 else np.array([])
        if np.isfinite(top_vals).any():
            mask = np.isfinite(top_vals)
            for L in range(int(start), int(end) + 1, 200):
                dif = np.abs(top_vals[mask] - L)
                if dif.size == 0:
                    continue
                j_rel = int(np.argmin(dif))
                if dif[j_rel] <= 100:
                    j = int(np.arange(len(top_vals))[mask][j_rel])
                    ann.append(dict(
                        x=float(xs_l[j]), y=float(y1), z=float(top_vals[j]),
                        text=f"{int(L)} m", showarrow=False, xanchor="center", yanchor="top",
                        font=dict(color="#ffffff", size=11), bgcolor="rgba(0,0,0,0.45)"
                    ))

    scene_cfg = dict(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        zaxis=dict(visible=False),
        aspectmode="data",
        uirevision=f"{rgi}|{scenario}|{var}",
    )
    if ann:
        scene_cfg["annotations"] = ann

    fig.update_layout(**base_layout(theme, scene=scene_cfg))

    return fig, note, hover, static, target_year


# =========================
# Timeseries
# =========================
def year_marker(year, theme):
    return [dict(type="line", xref="x", yref="paper", x0=year, x1=year, y0=0, y1=1,
                 line=dict(color=theme_of(theme)["fg"], width=1, dash="dot"), opacity=0.6)]


@app.callback(
    Output("glacier_timeseries", "figure"),
    Input("selected_rgi", "data"),
    Input("metric_var_select", "value"),
    Input("scenario", "value"),
    Input("theme", "data"),
    State("year_slider", "value"),
)
def update_timeseries(rgi, metric_var, scenario, theme, year):
    """
    Plot metrics over time for the selected glacier:
      - CORDEX RCPs (rcp_2_6, rcp_4_5, rcp_8_5) as 3 colored lines (selected scenario emphasised)
      - optional historical W5E5 as a grey dotted line (if present in the CSV)
      - a dotted marker at the year shown in the 3D view
    Uses METRICS_DF loaded from METRICS_TABLE_PATH.
    """
    t = theme_of(theme)

    def message(text):
        return go.Figure(layout=base_layout(
            theme,
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[{"text": text, "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False}],
        ))

    if not rgi or not metric_var:
        return message("Select a glacier to see metrics.")
    if METRICS_DF is None or len(METRICS_DF) == 0:
        return message(f"No metrics table loaded. Expected: {METRICS_TABLE_PATH}")
    if metric_var not in METRICS_DF.columns:
        return message(f"Metric '{metric_var}' not found in table.")

    d = METRICS_DF[METRICS_DF["rgi_id"] == str(rgi)].copy()
    d = d.dropna(subset=["year"])
    if len(d) == 0:
        return message("No rows for this glacier in the metrics table.")

    fig = go.Figure()

    # Historical (W5E5) if present
    if "source" in d.columns:
        dh = d[d["source"].str.lower().eq("w5e5")].sort_values("year")
        if len(dh) > 0:
            fig.add_trace(go.Scatter(
                x=dh["year"], y=dh[metric_var],
                mode="lines", name="Historical",
                line={"dash": "dot", "width": 2, "color": t["hist"]},
            ))

    # CORDEX scenarios
    dc = d[d["source"].str.lower().eq("cordex")].copy() if "source" in d.columns else d.copy()

    if "experiment" in dc.columns:
        for exp in SCENARIO_LABELS:
            de = dc[dc["experiment"].str.lower().eq(exp)].sort_values("year")
            if len(de) == 0:
                continue
            selected = exp == scenario
            fig.add_trace(go.Scatter(
                x=de["year"], y=de[metric_var],
                mode="lines", name=SCENARIO_LABELS[exp],
                line={"width": 3 if selected else 1.5, "color": t["rcp"][exp]},
                opacity=1.0 if selected else 0.55,
            ))
    else:
        # If experiment column is missing, just plot a single line
        dc = dc.sort_values("year")
        fig.add_trace(go.Scatter(x=dc["year"], y=dc[metric_var], mode="lines", name="Scenario", line={"width": 2.5}))

    axis = {"showgrid": True, "gridcolor": t["grid"], "zeroline": False, "linecolor": t["grid"]}
    fig.update_layout(**base_layout(
        theme,
        margin={"l": 8, "r": 14, "t": 6, "b": 8},
        hovermode="x unified",
        showlegend=False,  # legend is HTML above the chart (.ts-legend), so it can wrap
        # tick spacing is left to plotly so narrow charts get fewer labels;
        # no y-axis title because the metric dropdown right above names it
        xaxis={**axis, "automargin": True, "range": [YEARS[0] - 2, YEARS[-1] + 2], "tickangle": 0},
        yaxis={**axis, "automargin": True},
        shapes=year_marker(year, theme),
    ))
    return fig


@app.callback(
    Output("glacier_timeseries", "figure", allow_duplicate=True),
    Input("year_slider", "value"),
    State("theme", "data"),
    prevent_initial_call=True,
)
def move_year_marker(year, theme):
    patch = Patch()
    patch["layout"]["shapes"] = year_marker(year, theme)
    return patch


if __name__ == "__main__":
    # Local development only. In production the app is served by gunicorn
    # (see deploy/glacierdash.service), which imports `server` from this module.
    log("CSV:", GLACIERS_CSV)
    log("NC_DIR:", NC_DIR)
    log("Glaciers with data:", len(GLACIERS_DF))
    app.run(host=os.environ.get("DASH_HOST", "127.0.0.1"), port=int(os.environ.get("DASH_PORT", "8050")), debug=False)
