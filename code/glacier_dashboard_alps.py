import os, re, sys, glob, math, time, threading
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import json
from collections import OrderedDict

from dash import Dash, dcc, html, Input, Output, State, ctx
from dash.exceptions import PreventUpdate
import plotly.graph_objects as go


# =========================
# Paths (relative to /code)
# =========================
CODE_DIR = Path(__file__).resolve().parent           # ...\GitHub\code
REPO_DIR = CODE_DIR.parent                           # ...\GitHub
DATA_DIR = REPO_DIR / "data"

GLACIERS_CSV = DATA_DIR / "glacier_location_and_name" / "glaciers_region11_alps.csv"
NC_DIR = Path(os.environ.get("GLACIER_NC_DIR", DATA_DIR / "glacier_model_data"))

# (optional) precomputed volume/area table (keep optional; safe if missing)
VOLUME_TABLE_PATH = REPO_DIR.parent / "volume_and_area_table" / "volume_and_area_table.pkl"
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
        print("[metrics] Loaded rows:", len(METRICS_DF))
    except Exception as e:
        METRICS_DF = None
        log("[WARN] Failed to load metrics table:", METRICS_TABLE_PATH, "::", repr(e))
else:
    log("[WARN] Metrics table not found:", METRICS_TABLE_PATH)



# =========================
# Config
# =========================
FONT_FAMILY = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

# >>> ADDED: unified section heading style (bigger + consistent)
SECTION_H_STYLE = {"margin": "0 0 8px 0", "fontSize": "22px", "fontWeight": 700}

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


def log(*a):
    print("[alps-dashboard]", *a, file=sys.stdout, flush=True)


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


def open_nc(path: str):
    for eng in ("netcdf4", "h5netcdf"):
        if eng in AVAILABLE_ENGINES:
            try:
                return xr.open_dataset(path, engine=eng)
            except Exception:
                continue
    return xr.open_dataset(path)


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

def clear_ds_cache():
    """Close cached datasets if you ever need to reclaim memory."""
    for _, ds in list(_DS_CACHE.items()):
        try:
            ds.close()
        except Exception:
            pass
    _DS_CACHE.clear()


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


    sc_dim = "experiment" if ds.sizes.get("experiment", None) == 3 else detect_scenario_dim(ds, preferred_var=("usurf" if "usurf" in ds else var))
    if sc_dim is None:
        raise KeyError("Could not detect scenario dimension (expected a length-3 dim).")

    si = SCENARIO_TO_IDX.get(str(scenario_key), 1)

    if "topg" not in ds or "usurf" not in ds:
        raise KeyError("Dataset missing 'topg'/'usurf'")

    topg = ds["topg"].isel(**{k: v for k, v in {sc_dim: si, "time": ti}.items() if k in ds["topg"].dims}).values
    usurf = ds["usurf"].isel(**{k: v for k, v in {sc_dim: si, "time": ti}.items() if k in ds["usurf"].dims}).values

    Zvar  = get_var_or_fallback(ds, var, {"time": ti, sc_dim: si})

    if "thk" in ds:
        thk = ds["thk"].isel(**{k: v for k, v in {sc_dim: si, "time": ti}.items() if k in ds["thk"].dims}).values
    else:
        thk = usurf - topg

    xs, ys = coords_from_ds(ds, usurf)
    return topg, usurf, Zvar, thk, xs, ys, as_int(years[ti])

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


# year slider bounds (quick scan: try to open first file)
def infer_year_bounds():
    rgi = DEFAULT_RGI
    entry = DATA_INDEX[rgi]
    # pick any available file
    sample = entry.get("w5e5_mean") or (entry["chains"]["rcp_4_5"][0] if entry["chains"]["rcp_4_5"] else None)
    if not sample:
        return 2000, 2100
    try:
        with open_nc(sample) as ds:
            years = years_array_from_time(ds["time"].values)
            y0, y1 = int(np.nanmin(years)), int(np.nanmax(years))
            return y0, y1
    except Exception:
        return 2000, 2100



YEAR_MIN, YEAR_MAX = 2000, 2100

# Discrete 10-year steps (fixed)
YEARS = list(range(2000, 2101, 1))
YEARS_DISPLAY_5 = list(range(2000, 2101, 5))

DEFAULT_YEAR = 2020



# =========================
# Figures
# =========================



def make_globe_fig(dff: pd.DataFrame, selected_rgi: str | None):
    """Overview map with BW hillshade + orange borders + black oceans.

    - Uses Plotly MapLibre 'scattermap'
    - Hillshade is raster tiles
    - Borders are a GeoJSON line layer (fast + reliable)
    """
    fig = go.Figure()

    # Glacier markers
    fig.add_trace(go.Scattermap(
        lon=dff["cenlon"],
        lat=dff["cenlat"],
        mode="markers",
        marker=dict(size=7, color="#7dd3fc"),
        text=[f"{n} ({i})" if str(n).strip() else i for n, i in zip(dff["glac_name"], dff["rgi_id"])],
        customdata=dff["rgi_id"],
        hovertemplate="%{text}<extra></extra>",
        name="Glaciers",
    ))

    # Selected glacier marker
    if selected_rgi:
        sel = dff[dff["rgi_id"] == selected_rgi]
        if not sel.empty:
            s = sel.iloc[0]
            fig.add_trace(go.Scattermap(
                lon=[float(s.cenlon)],
                lat=[float(s.cenlat)],
                mode="markers",
                marker=dict(size=13, color="#f97316"),
                text=[f"Selected: {s.glac_name} ({s.rgi_id})" if str(s.glac_name).strip() else f"Selected: {s.rgi_id}"],
                customdata=[s.rgi_id],
                hovertemplate="%{text}<extra></extra>",
                name="Selected",
            ))

    # Borders layer source (line geometries)
    borders_lines = globals().get("BORDER_LINES_GJ", {"type": "FeatureCollection", "features": []})

    fig.update_layout(
        paper_bgcolor="#000",
        plot_bgcolor="#000",
        font=dict(color="#eaeaea", family=FONT_FAMILY),
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=0.01, xanchor="left", x=0.01),
        map=dict(
            # Dark basemap => oceans/background black-ish (your request)
            style="carto-darkmatter",
            center=dict(lat=46.25, lon=10.6),
            zoom=5.3,
            layers=[
                dict(
                    sourcetype="raster",
                    source=[
                        "https://services.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade_Dark/MapServer/tile/{z}/{y}/{x}"
                    ],
                    below="traces",
                    opacity=0.75,
                ),
                dict(
                    sourcetype="geojson",
                    source=borders_lines,
                    type="line",
                    color="#f6ad55",
                    line=dict(width=1.6),
                    below="traces",
                    opacity=1.0,
                ),
            ],
        ),
    )
    return fig


# =========================
# Assets (CSS) — robust across Dash versions (no html.Style)
# =========================
ASSETS_DIR = CODE_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

_UI_FIXES_CSS = r"""
/* =========================================================
   Force dropdown selected value + input text BLACK
   (covers multiple react-select versions / hashed classnames)
   ========================================================= */

/* Make control background white so black text is readable */
#metric_var_select .Select-control,
#metric_var_select .select__control,
#metric_var_select [class*="control"],
.dropdown-black .Select-control,
.dropdown-black .select__control,
.dropdown-black [class*="control"] {
  background-color: #fff !important;
}

/* Selected value text */
#metric_var_select .Select-value-label,
#metric_var_select .Select-value,
#metric_var_select .select__single-value,
#metric_var_select [class*="singleValue"],
#metric_var_select [class*="SingleValue"],
#metric_var_select [class*="ValueContainer"] div,
#metric_var_select [class*="valueContainer"] div,
#metric_var_select .css-1dimb5e-singleValue,
#metric_var_select .css-qc6sy-singleValue,
.dropdown-black .Select-value-label,
.dropdown-black .Select-value,
.dropdown-black .select__single-value,
.dropdown-black [class*="singleValue"],
.dropdown-black [class*="SingleValue"],
.dropdown-black [class*="ValueContainer"] div,
.dropdown-black [class*="valueContainer"] div {
  color: #000 !important;
}

/* Placeholder + typed text */
#metric_var_select .Select-placeholder,
#metric_var_select .select__placeholder,
#metric_var_select [class*="placeholder"],
#metric_var_select .Select-input input,
#metric_var_select .select__input input,
#metric_var_select input,
.dropdown-black .Select-placeholder,
.dropdown-black .select__placeholder,
.dropdown-black [class*="placeholder"],
.dropdown-black input {
  color: #000 !important;
}

/* Menu options */
#metric_var_select .Select-menu-outer,
#metric_var_select .Select-option,
#metric_var_select .VirtualizedSelectOption,
#metric_var_select .select__menu,
#metric_var_select .select__option,
.dropdown-black .Select-menu-outer,
.dropdown-black .Select-option,
.dropdown-black .VirtualizedSelectOption,
.dropdown-black .select__menu,
.dropdown-black .select__option {
  color: #000 !important;
  background-color: #fff !important;
}

/* Focused/selected option background (keep readable) */
#metric_var_select .is-focused,
#metric_var_select .is-selected,
#metric_var_select .select__option--is-focused,
#metric_var_select .select__option--is-selected,
.dropdown-black .is-focused,
.dropdown-black .is-selected,
.dropdown-black .select__option--is-focused,
.dropdown-black .select__option--is-selected {
  background-color: #f0f0f0 !important;
  color: #000 !important;
}

/* =========================================================
   Slider tooltip (selected year) text BLACK
   ========================================================= */
#year_slider .rc-slider-tooltip-inner,
#year_slider [class*="rc-slider-tooltip-inner"],
.slider-black-tooltip .rc-slider-tooltip-inner,
.slider-black-tooltip [class*="rc-slider-tooltip-inner"] {
  color: #000 !important;
  background: #fff !important;
}
"""


_css_path = ASSETS_DIR / "ui_fixes.css"
try:
    # Keep assets/ui_fixes.css consistent with code; only write when it changed
    # (the production service runs with a read-only home directory)
    if not _css_path.is_file() or _css_path.read_text(encoding="utf-8") != _UI_FIXES_CSS:
        _css_path.write_text(_UI_FIXES_CSS, encoding="utf-8")
except Exception as _e:
    log("[WARN] Failed to write assets CSS:", _css_path, "->", repr(_e))

# =========================
# App
# =========================
app = Dash(
    __name__,
    assets_folder=str(ASSETS_DIR),
    title="Glacier Dashboard — Alps (RGI 11)",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1, maximum-scale=1"}],
)
server = app.server  # WSGI entry point for gunicorn

app.layout = html.Div(
    style={"background": "#000", "color": "#eaeaea", "fontFamily": FONT_FAMILY, "padding": "10px"},
    children=[
        # Stores
        dcc.Store(id="selected_rgi", data=DEFAULT_RGI),
        dcc.Store(id="camera_mode", data="free"),
        dcc.Store(id="timelapse_on", data=False),
        dcc.Interval(id="timelapse_interval", interval=300, disabled=True),

        html.Div(
            style={"display": "flex", "gap": "10px", "alignItems": "stretch"},
            children=[
                # LEFT: Globe
                html.Div(
                    style={"flex": "1.2", "background": "#050505", "borderRadius": "18px", "padding": "10px"},
                    children=[
                        # >>> CHANGED: unified, bigger heading
                        html.H4("Alpine glaciers overview map", style=SECTION_H_STYLE),
                        dcc.Graph(
                            id="globe",
                            figure=make_globe_fig(GLACIERS_DF, DEFAULT_RGI),
                            style={"height": "52vh"},
                            config={"displayModeBar": False, "scrollZoom": True},
                        ),
                        html.Div(id="dbg", style={"fontSize": "12px", "opacity": 0.8, "marginTop": "6px"}),
                    ],
                ),

                # MIDDLE: Controls
                html.Div(
                    style={"flex": "0.9", "background": "#050505", "borderRadius": "14px", "padding": "10px"},
                    children=[
                        # >>> CHANGED: unified, bigger heading
                        html.H4("Controls", style=SECTION_H_STYLE),

                        html.H5("Search all modelled glaciers", style={"marginTop": "0px", "marginBottom": "6px"}),

                        dcc.Input(
                            id="search_query",
                            type="text",
                            value="",
                            placeholder="Search glacier name below",
                            debounce=False,
                            style={
                                "width": "100%",
                                "padding": "8px",
                                "borderRadius": "8px",
                                "border": "1px solid #444",
                                "background": "#0b0b0b",
                                "color": "#eaeaea",
                            },
                        ),

                        html.Div(style={"height": "8px"}),

                        dcc.Dropdown(
                            id="search_suggestions",
                            options=[],
                            value=None,
                            clearable=True,
                            placeholder="Suggestions…",
                            style={"color": "#111"},
                        ),

                        html.Div(style={"height": "10px", "borderBottom": "1px solid #222", "marginBottom": "10px"}),

                        html.Label("Country"),
                        dcc.Dropdown(
                            id="country_select",
                            options=[{"label": c, "value": c} for c in COUNTRIES],
                            value=DEFAULT_COUNTRY,
                            clearable=True,
                            style={"color": "#111"},
                        ),

                        html.Div(style={"height": "8px"}),

                        html.Label("Glacier"),
                        dcc.Dropdown(
                            id="rgi_select",
                            options=[],  # filled by callback
                            value=DEFAULT_RGI,
                            clearable=False,
                            style={"color": "#111"},
                        ),

                        html.Div(style={"height": "8px"}),

                        html.Label("Scenario"),
                        dcc.Dropdown(
                            id="scenario",
                            options=[{"label": SCENARIO_LABELS[k], "value": k} for k in ["rcp_2_6", "rcp_4_5", "rcp_8_5"]],
                            value=default_scenario_for(DEFAULT_RGI, DATA_INDEX),
                            clearable=False,
                            style={"color": "#111"},
                        ),

                        html.Div(style={"height": "8px"}),

                        html.Label("Property"),
                        dcc.Dropdown(
                            id="property",
                            options=[{"label": k, "value": k} for k in PROP_TO_VAR.keys()],
                            value="Thickness (m)",
                            clearable=False,
                            style={"color": "#111"},
                        ),
                    ],
                ),

                # RIGHT: Timeseries
                html.Div(
                    style={"flex": "1.0", "background": "#050505", "borderRadius": "18px", "padding": "10px"},
                    children=[
                        # >>> CHANGED: unified, bigger heading
                        html.H4("Projected parameters until 2100", style=SECTION_H_STYLE),
                        html.Div(
                            style={"display": "flex", "gap": "8px", "alignItems": "center", "marginBottom": "6px"},
                            children=[
                                html.Span("Variable:", style={"fontSize": "12px", "opacity": 0.85}),
                                dcc.Dropdown(
                                    id="metric_var_select",
                                    className="dropdown-black",
                                    options=[
                                        {"label": "Volume (km³)", "value": "volume_km3"},
                                        {"label": "Mean thickness (m)", "value": "thk_mean_m"},
                                        {"label": "SMB mean", "value": "smb_mean"},
                                        {"label": "Velocity mean", "value": "vel_mean"},
                                    ],
                                    value="volume_km3",
                                    clearable=False,
                                    searchable=False,
                                    style={"flex": "1"},
                                ),
                            ],
                        ),
                        dcc.Graph(
                            id="glacier_timeseries",
                            figure=go.Figure(layout=go.Layout(
                                title="Select a glacier",
                                paper_bgcolor="#000", plot_bgcolor="#000",
                                font=dict(color="#eaeaea", family=FONT_FAMILY)
                            )),
                            style={"height": "52vh"},
                            config={"displayModeBar": False},
                        ),
                    ],
                ),
            ],
        ),

        html.Div(style={"height": "10px"}),

        # Bottom: 3D
        html.Div(
            style={"background": "#050505", "borderRadius": "18px", "padding": "10px"},
            children=[
                # >>> CHANGED: unified, bigger heading
                html.H4("3D view of selected glacier", style=SECTION_H_STYLE),

                dcc.Graph(
                    id="mnt_surface",
                    style={"height": "55vh"},
                    config={"displayModeBar": False},
                    figure=go.Figure(),
                ),

                # --- 3D controls BELOW the 3D plot ---
                html.Div(
                    style={
                        "display": "flex",
                        "gap": "10px",
                        "alignItems": "center",
                        "flexWrap": "wrap",
                        "marginTop": "8px",
                    },
                    children=[
                        html.Button(
                            "Top-down view",
                            id="btn_topdown",
                            n_clicks=0,
                            style={"padding": "6px 10px", "borderRadius": "8px"},
                        ),
                        html.Button(
                            "▶ Timelapse",
                            id="btn_timelapse",
                            n_clicks=0,
                            style={"padding": "6px 10px", "borderRadius": "8px"},
                        ),

                        # >>> CHANGED: checklist label forced white
                        dcc.Checklist(
                            id="toggle_isohypses",
                            options=[{
                                "label": html.Span("Show 200m isohypses (edge labels)", style={"color": "#eaeaea"}),
                                "value": "iso"
                            }],
                            value=[],
                            style={"fontSize": "13px", "color": "#eaeaea"},
                        ),

                        html.Div(style={"flex": "1"}),

                        html.Div(
                            style={"minWidth": "360px"},
                            children=[
                                # >>> CHANGED: "Year" label text black
                                html.Div("Year", style={"fontSize": "12px", "opacity": 0.85, "color": "#000"}),

                                dcc.Slider(
                                    id="year_slider",
                                    className="slider-black-tooltip",
                                    min=min(YEARS),
                                    max=max(YEARS),
                                    step=1,
                                    value=DEFAULT_YEAR,
                                    marks={y: str(y) for y in range(min(YEARS), max(YEARS) + 1, 5)},
                                    tooltip={"placement": "bottom", "always_visible": False},
                                ),
                            ],
                        ),
                    ],
                ),

                html.Div(id="surface_note", style={"fontSize": "12px", "opacity": 0.85, "marginTop": "6px"}),
            ],
        ),
    ],
)


# =========================
# Callbacks
# =========================
@app.callback(
    Output("rgi_select", "options"),
    Output("rgi_select", "value"),
    Input("country_select", "value"),
    Input("selected_rgi", "data"),
)
def update_glacier_dropdown(country, selected_rgi):
    dff = GLACIERS_DF if not country else GLACIERS_DF[GLACIERS_DF["country"] == country]
    opts = []
    for row in dff.itertuples(index=False):
        label = f"{row.glac_name} ({row.rgi_id})" if str(row.glac_name).strip() else row.rgi_id
        opts.append({"label": label, "value": row.rgi_id})

    # choose value:
    values = set(dff["rgi_id"].tolist())
    if selected_rgi in values:
        val = selected_rgi
    elif opts:
        val = opts[0]["value"]
    else:
        val = None
    return opts, val






@app.callback(
    Output("search_suggestions", "options"),
    Output("search_suggestions", "value"),
    Input("search_query", "value"),
)
def update_search_suggestions(q):
    # Suggestions come from the CSV-backed table (GLACIERS_DF): search across ALL selectable glaciers.
    dff = GLACIERS_DF.copy()

    # Robust string columns
    dff["rgi_id"] = dff["rgi_id"].fillna("").astype(str)
    dff["glac_name"] = dff["glac_name"].fillna("").astype(str)
    if "country" in dff.columns:
        dff["country"] = dff["country"].fillna("").astype(str)
    else:
        dff["country"] = ""

    q = (q or "").strip().lower()

    # If empty query: show a diverse sample across countries (not biased by CSV order)
    if len(q) == 0:
        pieces = []
        for c, g in dff.groupby("country", sort=True):
            # take a few per country
            gg = g.sort_values(by=["glac_name", "rgi_id"]).head(380)
            pieces.append(gg)
        dff2 = (pd.concat(pieces, ignore_index=True) if pieces else dff).head(380)
    else:
        rgi_l = dff["rgi_id"].str.lower()
        name_l = dff["glac_name"].str.lower()

        contains = name_l.str.contains(q, na=False) | rgi_l.str.contains(q, na=False)
        dff2 = dff[contains].copy()
        if dff2.empty:
            return [], None

        # Rank: starts-with first, then shorter name
        starts = (
            dff2["glac_name"].str.lower().str.startswith(q, na=False)
            | dff2["rgi_id"].str.lower().str.startswith(q, na=False)
        )
        dff2["_starts"] = starts.astype(int)
        dff2["_name_len"] = dff2["glac_name"].str.len()

        # Diverse top-N per country, then merge
        pieces = []
        for c, g in dff2.groupby("country", sort=True):
            gg = g.sort_values(
                by=["_starts", "_name_len", "glac_name", "rgi_id"],
                ascending=[False, True, True, True],
            ).head(380)
            pieces.append(gg)

        dff2 = (pd.concat(pieces, ignore_index=True) if pieces else dff2).head(380)
        # final sort for nicer ordering
        dff2 = dff2.sort_values(
            by=["_starts", "country", "_name_len", "glac_name", "rgi_id"],
            ascending=[False, True, True, True, True],
        ).head(380)
    opts = []
    for row in dff2.itertuples(index=False):
        base = f"{row.glac_name} ({row.rgi_id})" if str(row.glac_name).strip() else row.rgi_id
        opts.append({"label": base, "value": row.rgi_id})

    return opts, None








@app.callback(
    Output("selected_rgi", "data"),
    Output("dbg", "children"),
    Output("country_select", "value"),
    Input("globe", "clickData"),
    Input("rgi_select", "value"),
    Input("search_suggestions", "value"),
    State("selected_rgi", "data"),
    State("country_select", "value"),
    prevent_initial_call=True,
)
def select_rgi_from_click_or_dropdown(clickData, rgi_dropdown, rgi_search, current_rgi, current_country):
    trig = (ctx.triggered[0]["prop_id"] if ctx.triggered else "")

    if trig == "globe.clickData":
        if not clickData or not clickData.get("points"):
            raise PreventUpdate
        rgi = clickData["points"][0].get("customdata")
        if not rgi:
            raise PreventUpdate
        return rgi, f"Selected from map: {rgi}", current_country

    if trig == "rgi_select.value":
        if not rgi_dropdown:
            raise PreventUpdate
        return rgi_dropdown, f"Selected from dropdown: {rgi_dropdown}", current_country

    if trig == "search_suggestions.value":
        if not rgi_search:
            raise PreventUpdate
        # set country so overview + dropdown list filter to that country
        try:
            row = GLACIERS_DF.loc[GLACIERS_DF["rgi_id"] == rgi_search].iloc[0]
            ctry = row["country"] if "country" in GLACIERS_DF.columns else current_country
        except Exception:
            ctry = current_country
        return rgi_search, f"Selected from search: {rgi_search}", ctry

    raise PreventUpdate

@app.callback(
    Output("globe", "figure"),
    Input("country_select", "value"),
    Input("selected_rgi", "data"),
)
def update_globe(country, selected_rgi):
    dff = GLACIERS_DF if not country else GLACIERS_DF[GLACIERS_DF["country"] == country]
    return make_globe_fig(dff, selected_rgi)




# =========================
# 3D UI controls (Top-down + Timelapse)
# =========================
@app.callback(
    Output("camera_mode", "data"),
    Input("btn_topdown", "n_clicks"),
    State("camera_mode", "data"),
    prevent_initial_call=True,
)
def toggle_topdown(n, mode):
    return "free" if mode == "topdown" else "topdown"


@app.callback(
    Output("timelapse_on", "data"),
    Output("timelapse_interval", "disabled"),
    Output("btn_timelapse", "children"),
    Input("btn_timelapse", "n_clicks"),
    State("timelapse_on", "data"),
    prevent_initial_call=True,
)
def toggle_timelapse(n, is_on):
    is_on = not bool(is_on)
    return is_on, (not is_on), ("⏸ Pause" if is_on else "▶ Timelapse")


@app.callback(
    Output("year_slider", "value"),
    Input("timelapse_interval", "n_intervals"),
    State("year_slider", "value"),
    prevent_initial_call=True,
)
def advance_year(n_intervals, current_year):
    if current_year not in YEARS:
        return YEARS[0]
    i = YEARS.index(current_year)
    return YEARS[(i + 1) % len(YEARS)]

def load_icemask_slice(nc_path: str, target_year: int, scenario_key: str, *args, **kwargs):
    """
    Return an ice mask slice for the given year/scenario.

    CORDEX 2D files often store time/x/y as (experiment, ...). This function
    slices those consistently. It is also tolerant to extra args passed by
    older call sites (ignored).
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



# ---- Manual LRU cache for heavy 3D per-frame arrays (safe with list-like Dash inputs) ----
_CACHED_3D_FIELDS = OrderedDict()
# ~1 MB per entry at MAX_SIDE=220; 128 covers a full timelapse run for one glacier/scenario/property
_CACHED_3D_FIELDS_MAX = int(os.environ.get("GLACIER_3D_CACHE_SIZE", "128"))
_CACHED_3D_LOCK = threading.Lock()

def _make_hashable(x):
    if isinstance(x, (list, tuple)):
        return tuple(_make_hashable(v) for v in x)
    if isinstance(x, dict):
        return tuple(sorted((k, _make_hashable(v)) for k, v in x.items()))
    return x

def _cached_3d_fields(rgi, scenario, var, target_year, max_side):
    key = (_make_hashable(rgi), _make_hashable(scenario), _make_hashable(var), int(target_year), int(max_side))
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
    source_file = os.path.basename(source_path)

    # downsample (CPU + WebGL payload)
    topg_ds, xs_ds, ys_ds = downsample(topg, xs, ys, int(max_side))
    usurf_ds, _, _        = downsample(usurf, xs, ys, int(max_side))
    prop_ds,  _, _        = downsample(prop_map, xs, ys, int(max_side))
    thk_ds,   _, _        = downsample(thk,   xs, ys, int(max_side))

    # glacier mask: use *year-specific* thickness so vanished ice becomes holes
    finite_thk = np.isfinite(thk_ds)
    eps = 0.5  # meters; increase if you want more aggressive "vanish" masking
    thk_mask = finite_thk & (thk_ds > eps)

    if icemask_full is not None:
        icemask_ds, _, _ = downsample(icemask_full, xs, ys, int(max_side))
        ice_mask = np.isfinite(icemask_ds) & (icemask_ds > 0.5)
        glacier_mask = thk_mask & ice_mask
    else:
        glacier_mask = thk_mask
    usurf_glacier = np.where(glacier_mask, usurf_ds, np.nan)
    prop_glacier  = np.where(glacier_mask, prop_ds,  np.nan)

    # defaults (masked: outside glacier -> NaN so it disappears)
    if str(var) in ("mean_temp", "t2m", "temp"):
        prop_field = np.where(glacier_mask, prop_ds, np.nan)
        top_z      = usurf_glacier
    else:
        prop_field = prop_glacier
        top_z      = usurf_glacier
    # robust color limits
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

    extra_note = ""
    colorscale = "Blues"
    cmin, cmax = _robust_limits(prop_field, default_span=10.0)

    # style by var
    if str(var) == "smb":
        colorscale = "RdBu"
        cmin, cmax = -10.0, 10.0
    elif str(var) in ("velsurf_mag", "velsurf"):
        colorscale = "Magma"
        cmin, cmax = _robust_limits(prop_field, default_span=1.0)
    elif str(var) in ("mean_temp", "t2m", "temp"):
        colorscale = "RdBu_r"
        lo, hi = _robust_limits(prop_field, default_span=5.0)
        vmax = max(abs(lo), abs(hi), 1.0)
        cmin, cmax = -vmax, vmax
    elif str(var) == "thk":
        colorscale = "Blues"
        lo, hi = _robust_limits(prop_field, default_span=10.0)
        cmin, cmax = 0.0, max(hi, 0.1)

    if (not np.isfinite(prop_field).any()) or not (cmax > cmin):
        prop_field = usurf_glacier.copy()
        colorscale = "Viridis"
        cmin, cmax = _robust_limits(prop_field, default_span=1.0)
        extra_note = " — (showing elevation: selected property missing/flat)"

    # center coords (stable WebGL)
    x_center = 0.5 * (float(xs_ds[0]) + float(xs_ds[-1]))
    y_center = 0.5 * (float(ys_ds[0]) + float(ys_ds[-1]))
    # 1D axes are enough for go.Surface (x -> columns, y -> rows); no meshgrid needed
    xs_l = np.asarray(xs_ds - x_center)
    ys_l = np.asarray(ys_ds - y_center)

    bedrock = np.where(np.isfinite(topg_ds), topg_ds, np.nan)

    res = (xs_l, ys_l, bedrock, top_z, prop_field, float(cmin), float(cmax), colorscale, source_file, extra_note, int(ysel))
    with _CACHED_3D_LOCK:
        _CACHED_3D_FIELDS[key] = res
        while len(_CACHED_3D_FIELDS) > _CACHED_3D_FIELDS_MAX:
            _CACHED_3D_FIELDS.popitem(last=False)
    return res


@app.callback(
    Output("mnt_surface", "figure"),
    Output("surface_note", "children"),
    Input("selected_rgi", "data"),
    Input("scenario", "value"),
    Input("property", "value"),
    Input("year_slider", "value"),
    Input("toggle_isohypses", "value"),
    Input("camera_mode", "data"),
)
def update_3d(rgi, scenario, property_label, target_year, show_iso_values, camera_mode):
    # ---- Normalize Dash list-like inputs (Checklist / multi-dropdown) ----
    def _first(x, default=None):
        if isinstance(x, (list, tuple)):
            return x[0] if len(x) else default
        return x

    rgi = _first(rgi, default=rgi)
    scenario = _first(scenario, default=scenario)
    property_label = _first(property_label, default=property_label)
    if not rgi:
        return go.Figure(layout=go.Layout(title="Pick a glacier (RGI)")), ""

    var = PROP_TO_VAR[property_label]


    # Dash safety: always return a 2-tuple
    # Heavy per-frame work is cached for smooth timelapse playback
    try:
        xs_l, ys_l, bedrock, top_z, prop_field, cmin, cmax, colorscale, source_file, extra_note, ysel = _cached_3d_fields(
            rgi, scenario, var, target_year, MAX_SIDE
        )
    except Exception as e:
        fig = go.Figure()
        fig.update_layout(
            title=f"3D Error: {type(e).__name__}: {e}",
            paper_bgcolor="#000", plot_bgcolor="#000",
        font=dict(color="#eaeaea", family=FONT_FAMILY),
        )
        return fig, f"Error loading frame: {repr(e)}"
    
    # isohypses on bedrock
    show_iso = isinstance(show_iso_values, (list, tuple)) and ("iso" in show_iso_values)
    zmin_bed = float(np.nanmin(bedrock)) if np.isfinite(bedrock).any() else 0.0
    zmax_bed = float(np.nanmax(bedrock)) if np.isfinite(bedrock).any() else zmin_bed
    start    = (np.floor(zmin_bed/200.0)*200.0) if zmax_bed > zmin_bed else zmin_bed
    end      = (np.ceil( zmax_bed/200.0)*200.0) if zmax_bed > zmin_bed else zmax_bed
    contour_cfg_bed = dict(z=dict(show=show_iso, start=start, end=end, size=200, color="#ffffff", width=2))

    BEDROCK_CS = [[0.0,"#303030"], [0.2,"#505050"], [0.5,"#808080"], [0.8,"#b8b8b8"], [1.0,"#d8d8d8"]]

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
        showscale=False, opacity=1.0, name="Base"
    ))

    # walls
    def add_wall(x2d, y2d, z1d_edge):
        Z = np.vstack([np.full_like(z1d_edge, base_z), np.nan_to_num(z1d_edge, nan=base_z)])
        fig.add_trace(go.Surface(
            x=x2d, y=y2d, z=Z,
            surfacecolor=Z, colorscale=BEDROCK_CS,
            cmin=zmin_bed, cmax=zmax_bed,
            showscale=False, opacity=1.0, name="Wall"
        ))


    add_wall(np.vstack([np.full_like(ys_l, x0), np.full_like(ys_l, x0)]), np.vstack([ys_l, ys_l]), bedrock[:, 0])
    add_wall(np.vstack([np.full_like(ys_l, x1), np.full_like(ys_l, x1)]), np.vstack([ys_l, ys_l]), bedrock[:, -1])
    add_wall(np.vstack([xs_l, xs_l]), np.vstack([np.full_like(xs_l, y0), np.full_like(xs_l, y0)]), bedrock[0, :])
    add_wall(np.vstack([xs_l, xs_l]), np.vstack([np.full_like(xs_l, y1), np.full_like(xs_l, y1)]), bedrock[-1, :])

    # bedrock surface
    fig.add_trace(go.Surface(
        x=xs_l, y=ys_l, z=bedrock, colorscale=BEDROCK_CS,
        showscale=False, opacity=1.0, name="Bedrock",
        contours=contour_cfg_bed
    ))

    # top surface with small z offset to avoid z-fighting
    _SURF_Z_EPS = 2.0
    top_z_plot = np.where(np.isfinite(top_z), top_z + _SURF_Z_EPS, top_z)

    fig.add_trace(go.Surface(
        x=xs_l, y=ys_l, z=top_z_plot,
        surfacecolor=prop_field, colorscale=colorscale,
        cmin=cmin, cmax=cmax, colorbar=dict(title=property_label, len=0.8),
        opacity=1.0, name=f"{property_label}"
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
        uirevision=f"{rgi}|{scenario}|{property_label}",
    )
    if ann:
        scene_cfg["annotations"] = ann

    fig.update_layout(
        title=f"{rgi} — {SCENARIO_LABELS.get(scenario, scenario)} — {property_label} @ {ysel}{extra_note}",
        scene=scene_cfg,
        paper_bgcolor="#000",
        plot_bgcolor="#000",
        font=dict(color="#eaeaea", size=14, family=FONT_FAMILY),
        margin=dict(l=0, r=0, t=36, b=0),
    )

    if camera_mode == "topdown":
        fig.update_layout(scene_camera=dict(
            eye=dict(x=0.0, y=0.0, z=2.5),
            up=dict(x=0, y=1, z=0),
        ))

    note = f"Source file: {source_file}"
    return fig, note

# =========================
# Timeseries
# =========================
@app.callback(
    Output("glacier_timeseries", "figure"),
    Input("selected_rgi", "data"),
    Input("metric_var_select", "value"),
)
def update_timeseries(rgi, metric_var):
    """
    Plot metrics over time for the selected glacier:
      - CORDEX RCPs (rcp_2_6, rcp_4_5, rcp_8_5) as 3 colored lines
      - optional historical W5E5 as a grey dotted line (if present in the CSV)
    Uses METRICS_DF loaded from METRICS_TABLE_PATH.
    """
    fig = go.Figure()

    if not rgi:
        fig.update_layout(
            paper_bgcolor="#000", plot_bgcolor="#000",
        font=dict(color="#eaeaea", family=FONT_FAMILY),
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[{"text": "Select a glacier to see metrics.", "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False}],
        )
        return fig

    if METRICS_DF is None or len(METRICS_DF) == 0:
        fig.update_layout(
            paper_bgcolor="#000", plot_bgcolor="#000",
        font=dict(color="#eaeaea", family=FONT_FAMILY),
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[{"text": f"No metrics table loaded. Expected: {METRICS_TABLE_PATH}", "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False}],
        )
        return fig

    if metric_var not in METRICS_DF.columns:
        fig.update_layout(
            paper_bgcolor="#000", plot_bgcolor="#000",
        font=dict(color="#eaeaea", family=FONT_FAMILY),
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[{"text": f"Metric '{metric_var}' not found in table.", "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False}],
        )
        return fig

    d = METRICS_DF[METRICS_DF["rgi_id"] == str(rgi)].copy()
    d = d.dropna(subset=["year"])
    if len(d) == 0:
        fig.update_layout(
            paper_bgcolor="#000", plot_bgcolor="#000",
        font=dict(color="#eaeaea", family=FONT_FAMILY),
            margin={"l": 10, "r": 10, "t": 10, "b": 10},
            xaxis={"visible": False},
            yaxis={"visible": False},
            annotations=[{"text": "No rows for this glacier in the metrics table.", "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5, "showarrow": False}],
        )
        return fig

    # Scenario colors: hottest is light red, then orange, then yellow
    rcp_colors = {
        "rcp_2_6": "rgba(204,163,0,0.95)",  # dark yellow
        "rcp_4_5": "rgba(255,140,0,0.95)",  # light orange
        "rcp_8_5": "rgba(255,102,102,0.9)",  # light red
    }

    # Historical (W5E5) if present
    if "source" in d.columns:
        dh = d[d["source"].str.lower().eq("w5e5")].sort_values("year")
        if len(dh) > 0:
            fig.add_trace(
                go.Scatter(
                    x=dh["year"],
                    y=dh[metric_var],
                    mode="lines+markers",
                    name="W5E5 (historical)",
                    line={"dash": "dot", "width": 2, "color": "rgba(180,180,180,0.9)"},
                    marker={"size": 5},
                )
            )

    # CORDEX scenarios
    if "source" in d.columns:
        dc = d[d["source"].str.lower().eq("cordex")].copy()
    else:
        dc = d.copy()

    if "experiment" in dc.columns:
        for exp in ["rcp_2_6", "rcp_4_5", "rcp_8_5"]:
            de = dc[dc["experiment"].str.lower().eq(exp)].sort_values("year")
            if len(de) == 0:
                continue
            fig.add_trace(
                go.Scatter(
                    x=de["year"],
                    y=de[metric_var],
                    mode="lines+markers",
                    name=exp.upper().replace("_", "."),
                    line={"width": 2.5, "color": rcp_colors.get(exp, None)},
                    marker={"size": 5},
                )
            )
    else:
        # If experiment column is missing, just plot a single line
        dc = dc.sort_values("year")
        fig.add_trace(
            go.Scatter(
                x=dc["year"],
                y=dc[metric_var],
                mode="lines+markers",
                name="Scenario",
                line={"width": 2.5},
                marker={"size": 5},
            )
        )

    # Labels
    y_labels = {
        "volume_km3": "Volume (km³)",
        "area_km2": "Area (km²)",
        "thk_mean_m": "Mean thickness (m)",
        "smb_mean": "SMB mean",
        "vel_mean": "Velocity mean",
    }
    fig.update_layout(
        paper_bgcolor="#000", plot_bgcolor="#000",
        font=dict(color="#eaeaea", family=FONT_FAMILY),
        margin={"l": 40, "r": 10, "t": 10, "b": 30},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0.0,
                "font": {"family": FONT_FAMILY, "color": "#eaeaea"}},
        xaxis={"title": {"text": "Year", "font": {"family": FONT_FAMILY, "color": "#eaeaea"}},
               "showgrid": True, "gridcolor": "#222", "zeroline": False,
               "tickfont": {"family": FONT_FAMILY, "color": "#eaeaea"}},
        yaxis={"title": {"text": y_labels.get(metric_var, metric_var), "font": {"family": FONT_FAMILY, "color": "#eaeaea"}},
               "showgrid": True, "gridcolor": "#222", "zeroline": False,
               "tickfont": {"family": FONT_FAMILY, "color": "#eaeaea"}},
    )
    return fig


if __name__ == "__main__":
    # Local development only. In production the app is served by gunicorn
    # (see deploy/glacierdash.service), which imports `server` from this module.
    log("CSV:", GLACIERS_CSV)
    log("NC_DIR:", NC_DIR)
    log("Glaciers with data:", len(GLACIERS_DF))
    app.run(host=os.environ.get("DASH_HOST", "127.0.0.1"), port=int(os.environ.get("DASH_PORT", "8050")), debug=False)
