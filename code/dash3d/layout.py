"""Page metadata, the configuration handed to map3d.js, and the Dash layout, in English (/) and German (/de)."""
import os

from dash import Input, Output, callback, dcc, html

from . import config
import legal

from .api import BUILD, PEAKS_URL
from .colours import VAR_STYLE, colour_stops, var_config
from .store import ALPS_BOUNDS, GLACIERS
from .terrain import DEM_MAXZOOM, TERRAIN_VERSION

# page description and the preview shown when a link is shared (Mastodon, Slack, LinkedIn, messengers)
SITE_URL = os.environ.get("SITE_URL", "https://www.glacier-evolution.nat.fau.de").rstrip("/")
PAGE_TITLE = "Alpine glacier evolution — RGI 11"
PAGE_TITLE_DE = "Entwicklung der Alpengletscher — RGI 11"
PAGE_DESCRIPTION = ("Interactive 3D map of 380 glaciers in the European Alps from 2000 to 2100 under three "
                    "greenhouse gas scenarios (RCP 2.6, 4.5, 8.5): ice thickness, flow speed, mass balance and "
                    "temperature, modelled at FAU Erlangen-Nürnberg.")
PREVIEW_IMAGE = "preview.jpg"                     # 1200 x 630, in code/assets

META_TAGS = [
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
]


def glaciers_geojson():
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [float(r.cenlon), float(r.cenlat)]},
        "properties": {"rgi": r.rgi_id, "name": config.GLACIER_NAMES.get(r.rgi_id, ""), "country": r.country},
    } for r in config.GLACIERS_DF.itertuples()]}


SEARCH_HITS = 5                     # at most this many search results, no scrolling

# the interface texts per language. Those used in the browser (clientside.py, map3d.js) go there in
# map_config ("t").
N = len(GLACIERS)
TEXTS = {
    "en": {
        "cbar_title": "Click to choose what the colours show",
        "search": "Search glaciers and peaks…",
        "show_all": "Show all glaciers",
        "title": "Alpine glaciers in ", "title_tip": "Back to all glaciers",
        "theme": "Switch light/dark theme",
        "intro_head": f"{N} Alpine glaciers, modelled from 2000 to 2100",
        "intro": " under three greenhouse gas scenarios. Press ▶ to watch them change, click a glacier for its "
                 "numbers, or click the colour bar to show flow speed, mass balance or temperature.",
        "intro_ok": "Got it",
        "scenario": "RCP scenario", "scenario_tip": "Click to choose the greenhouse gas scenario",
        "play": " Play", "pause": " Pause", "play_tip": "Play or pause the years",
        "funded": "Funded by the European Union · European Research Council",
        # in the browser
        "more": "more – keep typing", "none": "Nothing found", "peak": "Peak",
        "peak_tip": "Click to stand on the summit",
        "peak_exit": "Leave summit", "peak_exit_tip": "Back to the glacier view",
        "update": "A new version of this page is available.", "reload": "Reload",
        "compass_peak": "Back to the first view from the summit",
        "in": "in", "of": "of", "gone": "gone", "decimal": ".",
        "thickness": "Thickness", "compass": "Drag to rotate the map; click to turn north and flat, click again to turn back",
    },
    "de": {
        "cbar_title": "Klicken, um zu wählen, was die Farben zeigen",
        "search": "Gletscher und Gipfel suchen…",
        "show_all": "Alle Gletscher zeigen",
        "title": "Alpengletscher ", "title_tip": "Zurück zu allen Gletschern",
        "theme": "Hell/dunkel umschalten",
        "intro_head": f"{N} Alpengletscher, modelliert von 2000 bis 2100",
        "intro": " unter drei Treibhausgas-Szenarien. Drücken Sie ▶, um ihre Entwicklung zu sehen, klicken Sie auf "
                 "einen Gletscher für seine Zahlen oder auf die Farbskala, um Fließgeschwindigkeit, Massenbilanz "
                 "oder Temperatur zu zeigen.",
        "intro_ok": "Verstanden",
        "scenario": "RCP-Szenario", "scenario_tip": "Klicken, um das Treibhausgas-Szenario zu wählen",
        "play": " Start", "pause": " Pause", "play_tip": "Jahre abspielen oder anhalten",
        "funded": "Gefördert von der Europäischen Union · Europäischer Forschungsrat",
        "more": "weitere – weiter tippen", "none": "Nichts gefunden", "peak": "Gipfel",
        "peak_tip": "Klicken, um auf dem Gipfel zu stehen",
        "peak_exit": "Gipfel verlassen", "peak_exit_tip": "Zurück zur Gletscheransicht",
        "update": "Eine neue Version dieser Seite ist verfügbar.", "reload": "Neu laden",
        "compass_peak": "Zurück zur ersten Ansicht vom Gipfel",
        "in": "im Jahr", "of": "von", "gone": "verschwunden", "decimal": ",",
        "thickness": "Eisdicke", "compass": "Ziehen, um die Karte zu drehen; klicken für Norden und flach, erneut klicken für zurück",
    },
}
BROWSER_TEXTS = ("more", "none", "peak", "peak_tip", "update", "reload", "compass_peak", "in", "of", "gone",
                 "decimal", "thickness", "theme", "play", "pause", "compass")

# [rgi, name] of all glaciers, named ones first (alphabetically), then the unnamed ones by RGI ID
SEARCH_LIST = sorted(([r, config.GLACIER_NAMES.get(r, "")] for r in GLACIERS),
                     key=lambda e: (not e[1], e[1].lower(), e[0]))



def play_label(lang):
    """Play button: the word is hidden on phones (style3d.css); clientside.py swaps the pair for ⏸ Pause."""
    return [html.Span("▶", className="play-icon"), html.Span(TEXTS[lang]["play"], className="play-word")]


_MAP_CONFIG = {
    "alps_bounds": ALPS_BOUNDS,
    "dem_maxzoom": DEM_MAXZOOM,
    "terrain_url": f"/api3d/terrain/{TERRAIN_VERSION}/{{z}}/{{x}}/{{y}}.png",
    "glaciers": glaciers_geojson(),
    "meshes": {r: {**g.mesh_info(), "k": k} for k, (r, g) in enumerate(GLACIERS.items())},
    "scenario_labels": config.SCENARIO_LABELS,
    "search": SEARCH_LIST,
    "version": BUILD,                  # map3d.js offers a reload when the server's differs (api.py)
    "peaks_url": PEAKS_URL if config.PEAKS else None,   # loaded when the search is first used (map3d.js)
    "years": [config.YEARS[0], config.YEARS[-1]],
}
MAP_CONFIG = {lang: {**_MAP_CONFIG, "lang": lang, "vars": {v: var_config(v, lang) for v in VAR_STYLE},
                     "cities": [[en if lang == "en" else de, lon, lat] for en, de, lon, lat in config.CITIES],
                     "t": {k: TEXTS[lang][k] for k in BROWSER_TEXTS}} for lang in config.LANGS}


def lang_of(path):
    return "de" if (path or "").rstrip("/").endswith("/de") else "en"


def make_layout(app):
    """The page is filled in by language from the address: / English, /de German."""
    pages = {lang: make_page(app, lang) for lang in config.LANGS}

    @callback(Output("page", "children"), Input("url", "pathname"))
    def render_page(path):
        return pages[lang_of(path)]

    return html.Div([dcc.Location(id="url", refresh=False), html.Div(id="page")])


def make_page(app, lang):
    T = TEXTS[lang]
    return html.Div(
        className="app3d",
        lang=lang,
        children=[
            dcc.Store(id="url_sync"),
            dcc.Store(id="selected_rgi"),
            dcc.Store(id="rgi_select"),        # a glacier picked in the search or on the map: {rgi, t}
            dcc.Store(id="glacier_hits"),      # the IDs (RGI or peak) shown in the search results
            dcc.Store(id="peaks_ready"),       # set by map3d.js once the peak list has loaded
            dcc.Store(id="peak_sel"),          # the summit the camera stands on (map3d.js): {id, name, sub}
            dcc.Store(id="property"),          # chosen by clicking the colour bar
            dcc.Store(id="scenario"),          # chosen with the scenario buttons
            dcc.Store(id="map_config", data=MAP_CONFIG[lang]),
            dcc.Store(id="series_data"),       # area of the selection per scenario and year, for the scenario switch
            dcc.Store(id="theme", data="dark", storage_type="local"),
            dcc.Interval(id="timelapse_interval", interval=250, disabled=True),

            html.Div(id="map3d"),

            # top: the panel with the spatial choices, property (colour bar) and glacier search (one row where there
            # is room, else stacked), and below it the title with the year as plain text
            html.Div(className="top-stack", children=[
                html.Section(className="float title-card", children=[
                    # colour bar = property switch; its menu unfolds below it
                    html.Div(className="cbar-wrap", children=[
                        html.Div(id="cbar_btn", className="cbar-btn", n_clicks=0, role="button", tabIndex="0",
                                 title=T["cbar_title"],
                                 children=html.Div(id="colourbar", className="cbar")),
                        html.Div(id="prop_menu", className="prop-menu is-hidden", children=[
                            html.Button(id={"type": "prop_opt", "index": var}, n_clicks=0, className="prop-opt",
                                        children=[
                                html.Span(label, className="prop-opt-label"),
                                html.Span(className="prop-opt-ramp", style={
                                    "background": f"linear-gradient(to right, {', '.join(colour_stops(var, 9))})"}),
                            ]) for var, label in config.VAR_LABELS[lang].items() if var in VAR_STYLE
                        ]),
                    ]),
                    # glacier search: type in the field, up to 5 matches below it (clientside.py fills them in)
                    html.Div(id="glacier_box", className="glacier-search", children=[
                        dcc.Input(id="glacier_search", type="text", inputMode="search", value="", placeholder=T["search"],
                                  autoComplete="off", spellCheck=False, n_submit=0, className="gs-input"),
                        # the selected glacier, laid out like a search result (name, RGI ID); hidden while editing
                        html.Div(className="gs-display", **{"aria-hidden": "true"}, children=[
                            html.Span(id="glacier_display_name", className="gs-name"),
                            html.Span(id="glacier_display_id", className="gs-id"),
                        ]),
                        html.Button("×", id="glacier_clear", n_clicks=0, className="gs-clear is-hidden",
                                    title=T["show_all"], **{"aria-label": T["show_all"]}),
                        html.Div(id="glacier_results", className="gs-results is-empty", role="listbox", children=[
                            *[html.Div(id={"type": "ghit", "index": i}, n_clicks=0, role="option", tabIndex="-1",
                                       className="gs-hit is-hidden", children=[
                                html.Span(id={"type": "ghit_name", "index": i}, className="gs-name"),
                                html.Span(id={"type": "ghit_id", "index": i}, className="gs-id"),
                            ]) for i in range(SEARCH_HITS)],
                            html.Div(id="glacier_more", className="gs-more"),
                        ]),
                    ]),
                ]),
                # the title, plain text on the map below the panel
                html.H1(className="map-title", title=T["title_tip"], children=[
                    T["title"], html.Span(str(config.DEFAULT_YEAR), id="year_label", className="year-label")]),
            ]),

            # theme switch; hidden, map3d.js shows it as a map control under the zoom buttons
            html.Button("☀", id="theme_toggle", n_clicks=0, className="theme-btn",
                        title=T["theme"], **{"aria-label": T["theme"]}),

            # bottom column, stacked from the bottom up: dock, first-visit hint.
            # The column lets clicks through to the map; only its cards take them.
            html.Div(className="bottom-stack", children=[
                # first-visit hint; map3d.js shows it unless the visitor closed it before
                html.Div(id="intro", className="float intro is-hidden", role="note", children=[
                    html.P([
                        html.Strong(T["intro_head"]), T["intro"],
                    ]),
                    html.Button(T["intro_ok"], id="intro_close", className="btn"),
                ]),

                # on a summit (map3d.js, peak_sel): the way back down, bottom right above the dock; clientside.py shows it and calls leavePeak
                html.Button(id="peak_exit", n_clicks=0, className="peak-exit is-hidden", title=T["peak_exit_tip"],
                            children=[html.Span("↩", className="peak-exit-icon", **{"aria-hidden": "true"}),
                                      T["peak_exit"]]),

                # the temporal choices in one line: play, slider, scenario
                html.Div(className="float dock", children=[
                    html.Div(className="dock-view", children=[
                        # scenario switch like the colour bar: shows the chosen scenario and the selection's area in
                        # the displayed year; its menu (all scenarios) opens above it
                        html.Div(className="sc-wrap", children=[
                            html.Div(id="sc_menu", className="sc-menu is-hidden", role="listbox",
                                     **{"aria-label": T["scenario"]}, children=[
                                html.Button(id={"type": "sc_opt", "index": key}, n_clicks=0, className="sc-opt",
                                            children=[
                                    html.Span(label, className="sc-opt-label"),
                                    html.Span(id={"type": "sc_val", "index": key}, className="sc-opt-value"),
                                ]) for key, label in config.SCENARIO_LABELS.items()
                            ]),
                            html.Button(id="sc_btn", n_clicks=0, className="sc-btn",
                                        title=T["scenario_tip"]),
                        ]),
                    ]),
                    html.Div(className="dock-time", children=[
                        html.Button(play_label(lang), id="btn_timelapse", n_clicks=0, className="btn play-btn",
                                    title=T["play_tip"], **{"aria-label": T["play_tip"]}),
                        html.Div(className="year", children=dcc.Slider(
                            id="year_slider", min=min(config.YEARS), max=max(config.YEARS), step=1,
                            value=config.DEFAULT_YEAR,
                            marks={y: str(y) for y in range(min(config.YEARS), max(config.YEARS) + 1, 25)},
                            allow_direct_input=False, updatemode="drag")),
                    ]),
                ]),
            ]),

            html.Div(className="legal-corner", children=[legal.sources_link(lang), legal.legal_links(lang)]),
            html.Div(className="logo-corner", children=[
                html.Img(src=app.get_asset_url(config.LOGO_FAU), className="logo-fau",
                         alt="Friedrich-Alexander-Universität Erlangen-Nürnberg"),
                html.Img(src=app.get_asset_url(config.LOGO_ERC), className="logo-erc logo-erc-dark",
                         alt=T["funded"]),
                html.Img(src=app.get_asset_url(config.LOGO_ERC_LIGHT), className="logo-erc logo-erc-light",
                         alt=T["funded"]),
            ]),
        ],
    )
