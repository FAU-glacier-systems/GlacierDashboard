"""Paths, the glacier list and names, properties, scenarios and years, and the page template."""
import datetime
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

import pandas as pd

CODE_DIR = Path(__file__).resolve().parent.parent           # .../dashboard/code
DATA_DIR = CODE_DIR.parent / "data"
GLACIERS_CSV = DATA_DIR / "glacier_location_and_name" / "glaciers_region11_alps.csv"
STORE_DIR = Path(os.environ.get("GLACIER_STORE_DIR", DATA_DIR / "glacier_store"))
PEAKS_JSON = DATA_DIR / "peaks" / "peaks_region11_alps.json"     # tools/build_peaks.py


def log(*a):
    print("[alps-dashboard]", *a, file=sys.stdout, flush=True)


# the dashboard in English at /, in German at /de
LANGS = ("en", "de")

# property labels per language, in menu order; the unit in brackets (map3d.js splits it off for the hover label)
VAR_LABELS = {
    "en": {
        "thk": "Thickness (m)",
        "velsurf_mag": "Velocity (m/a)",
        "smb": "Surface Mass Balance (m/a)",
        "mean_temp": "Mean Temperature (°C)",
    },
    "de": {
        "thk": "Eisdicke (m)",
        "velsurf_mag": "Fließgeschwindigkeit (m/a)",
        "smb": "Oberflächenmassenbilanz (m/a)",
        "mean_temp": "Mitteltemperatur (°C)",
    },
}
DEFAULT_VAR = "thk"

SCENARIO_LABELS = {"rcp_2_6": "RCP 2.6", "rcp_4_5": "RCP 4.5", "rcp_8_5": "RCP 8.5"}
SCENARIO_TO_IDX = {k: i for i, k in enumerate(SCENARIO_LABELS)}
DEFAULT_SCENARIO = "rcp_4_5"

YEARS = list(range(2000, 2101))     # model years (annual steps)


def default_year():
    """The current year (within the model years): what a visitor sees without a year in the link."""
    return min(max(datetime.date.today().year, YEARS[0]), YEARS[-1])


DEFAULT_YEAR = default_year()       # at startup, for the layout; set_year picks the current one on every visit

# The glacier list: the CSV, limited to the glaciers in the store (tools/build_glacier_store.py)
GLACIERS_DF = pd.read_csv(GLACIERS_CSV, encoding="utf-8-sig")
GLACIERS_DF = GLACIERS_DF[[(STORE_DIR / r / "meta.json").is_file() for r in GLACIERS_DF.rgi_id]].copy()
if GLACIERS_DF.empty:
    raise RuntimeError(f"No glaciers from {GLACIERS_CSV.name} found in {STORE_DIR}")
ALL_RGIS = set(GLACIERS_DF.rgi_id)
GLACIER_NAMES = {r: n.strip() for r, n in zip(GLACIERS_DF.rgi_id, GLACIERS_DF.glac_name.fillna("")) if n.strip()}

# notable peaks near the glaciers, for the search (the camera goes onto the summit); none if the file is missing
try:
    PEAKS = [p for p in json.loads(PEAKS_JSON.read_text(encoding="utf-8"))["peaks"] if p["glacier"] in ALL_RGIS]
except FileNotFoundError:
    log("no peak list at", PEAKS_JSON, "- the search shows glaciers only")
    PEAKS = []

# cities labelled on the map for orientation (map3d.js): (English name, German name, lon, lat)
CITIES = [
    ("Zürich", "Zürich", 8.5417, 47.3769), ("Bern", "Bern", 7.4474, 46.9480), ("Lucerne", "Luzern", 8.3093, 47.0502),
    ("Geneva", "Genf", 6.1432, 46.2044), ("Lausanne", "Lausanne", 6.6323, 46.5197), ("Sion", "Sitten", 7.3606, 46.2331),
    ("Brig", "Brig", 7.9876, 46.3159), ("Zermatt", "Zermatt", 7.7491, 46.0207),
    ("Interlaken", "Interlaken", 7.8632, 46.6863), ("Chur", "Chur", 9.5329, 46.8499),
    ("St. Moritz", "St. Moritz", 9.8355, 46.4908), ("Innsbruck", "Innsbruck", 11.4041, 47.2692),
    ("Salzburg", "Salzburg", 13.0550, 47.8095), ("Lienz", "Lienz", 12.7696, 46.8297),
    ("Bolzano / Bozen", "Bozen / Bolzano", 11.3548, 46.4983), ("Merano / Meran", "Meran / Merano", 11.1594, 46.6713),
    ("Bressanone / Brixen", "Brixen / Bressanone", 11.6560, 46.7150), ("Trento", "Trient", 11.1217, 46.0748),
    ("Sondrio", "Sondrio", 9.8782, 46.1699), ("Milan", "Mailand", 9.1900, 45.4642), ("Turin", "Turin", 7.6869, 45.0703),
    ("Aosta", "Aosta", 7.3201, 45.7370), ("Chamonix", "Chamonix", 6.8694, 45.9237), ("Annecy", "Annecy", 6.1294, 45.8992),
    ("Grenoble", "Grenoble", 5.7245, 45.1885),
]

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
