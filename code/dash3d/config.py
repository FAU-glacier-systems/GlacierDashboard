"""Paths, the glacier list and names, properties, scenarios and years, and the page template."""
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

import pandas as pd

CODE_DIR = Path(__file__).resolve().parent.parent           # .../dashboard/code
DATA_DIR = CODE_DIR.parent / "data"
GLACIERS_CSV = DATA_DIR / "glacier_location_and_name" / "glaciers_region11_alps.csv"
STORE_DIR = Path(os.environ.get("GLACIER_STORE_DIR", DATA_DIR / "glacier_store"))


def log(*a):
    print("[alps-dashboard]", *a, file=sys.stdout, flush=True)


PROP_TO_VAR = {
    "Thickness (m)": "thk",
    "Velocity (m/a)": "velsurf_mag",
    "Surface Mass Balance (m/a)": "smb",
    "Mean Temperature (°C)": "mean_temp",
}
VAR_TO_PROP = {v: k for k, v in PROP_TO_VAR.items()}
DEFAULT_VAR = "thk"

SCENARIO_LABELS = {"rcp_2_6": "RCP 2.6", "rcp_4_5": "RCP 4.5", "rcp_8_5": "RCP 8.5"}
SCENARIO_TO_IDX = {k: i for i, k in enumerate(SCENARIO_LABELS)}
DEFAULT_SCENARIO = "rcp_4_5"

YEARS = list(range(2000, 2101))     # model years (annual steps)
DEFAULT_YEAR = 2020

# The glacier list: the CSV, limited to the glaciers in the store (tools/build_glacier_store.py)
GLACIERS_DF = pd.read_csv(GLACIERS_CSV, encoding="utf-8-sig")
GLACIERS_DF = GLACIERS_DF[[(STORE_DIR / r / "meta.json").is_file() for r in GLACIERS_DF.rgi_id]].copy()
if GLACIERS_DF.empty:
    raise RuntimeError(f"No glaciers from {GLACIERS_CSV.name} found in {STORE_DIR}")
ALL_RGIS = set(GLACIERS_DF.rgi_id)
GLACIER_NAMES = {r: n.strip() for r, n in zip(GLACIERS_DF.rgi_id, GLACIERS_DF.glac_name.fillna("")) if n.strip()}

# glacier shown on page load: the Great Aletsch Glacier
DEFAULT_RGI = "RGI2000-v7.0-G-11-02596"
if DEFAULT_RGI not in ALL_RGIS:
    DEFAULT_RGI = min(ALL_RGIS)


def url_params(search):
    """Query string -> {key: first value}."""
    return {k: v[0] for k, v in parse_qs((search or "").lstrip("?")).items() if v}


# Styling lives in assets/style.css and assets3d/style3d.css. The theme is a data-theme attribute on <html>;
# this inline script applies the saved theme before first paint so there is no flash.
INDEX_STRING = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
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

LOGO_FAU = "Friedrich-Alexander-Universität_Erlangen-Nürnberg_Logo_07.2022.svg.png"
LOGO_ERC = "LOGO_ERC-FLAG_EU-no text.png"
LOGO_ERC_LIGHT = "LOGO_ERC-FLAG_EU-no text-light.png"     # transparent background, dark text
