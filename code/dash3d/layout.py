"""Page metadata, the configuration handed to map3d.js, and the Dash layout."""
import os

from dash import dcc, html

from . import config
import legal

from .colours import VAR_STYLE, colour_stops, var_config
from .store import ALPS_BOUNDS, GLACIERS
from .terrain import DEM_MAXZOOM, TERRAIN_VERSION

# page description and the preview shown when a link is shared (Mastodon, Slack, LinkedIn, messengers)
SITE_URL = os.environ.get("SITE_URL", "https://www.glacier-evolution.nat.fau.de").rstrip("/")
PAGE_TITLE = "Alpine glacier evolution — RGI 11"
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

# [rgi, name] of all glaciers, named ones first (alphabetically), then the unnamed ones by RGI ID
SEARCH_LIST = sorted(([r, config.GLACIER_NAMES.get(r, "")] for r in GLACIERS),
                     key=lambda e: (not e[1], e[1].lower(), e[0]))

# play button: the word is hidden on phones (style3d.css); clientside.py swaps the pair for ⏸ Pause
PLAY_LABEL = [html.Span("▶", className="play-icon"), html.Span(" Play", className="play-word")]

MAP_CONFIG = {
    "alps_bounds": ALPS_BOUNDS,
    "dem_maxzoom": DEM_MAXZOOM,
    "terrain_url": f"/api3d/terrain/{TERRAIN_VERSION}/{{z}}/{{x}}/{{y}}.png",
    "glaciers": glaciers_geojson(),
    "meshes": {r: {**g.mesh_info(), "k": k} for k, (r, g) in enumerate(GLACIERS.items())},
    "vars": {v: var_config(v) for v in VAR_STYLE},
    "scenario_labels": config.SCENARIO_LABELS,
    "search": SEARCH_LIST,
    "years": [config.YEARS[0], config.YEARS[-1]],
}


def make_layout(app):
    return html.Div(
        className="app3d",
        children=[
            dcc.Location(id="url", refresh=False),
            dcc.Store(id="url_sync"),
            dcc.Store(id="selected_rgi"),
            dcc.Store(id="rgi_select"),        # a glacier picked in the search or on the map: {rgi, t}
            dcc.Store(id="glacier_hits"),      # the RGI IDs shown in the search results
            dcc.Store(id="property"),          # chosen by clicking the colour bar
            dcc.Store(id="scenario"),          # chosen with the scenario buttons
            dcc.Store(id="map_config", data=MAP_CONFIG),
            dcc.Store(id="series_data"),       # volume and area of the selection, for the numbers under the colour bar
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
                                 title="Click to choose what the colours show",
                                 children=html.Div(id="colourbar", className="cbar")),
                        html.Div(id="prop_menu", className="prop-menu is-hidden", children=[
                            html.Button(id={"type": "prop_opt", "index": var}, n_clicks=0, className="prop-opt",
                                        children=[
                                html.Span(label, className="prop-opt-label"),
                                html.Span(className="prop-opt-ramp", style={
                                    "background": f"linear-gradient(to right, {', '.join(colour_stops(var, 9))})"}),
                            ]) for label, var in config.PROP_TO_VAR.items() if var in VAR_STYLE
                        ]),
                    ]),
                    # glacier search: type in the field, up to 5 matches below it (clientside.py fills them in)
                    html.Div(id="glacier_box", className="glacier-search", children=[
                        dcc.Input(id="glacier_search", type="text", inputMode="search", value="", placeholder=f"Search {len(GLACIERS)} glaciers…",
                                  autoComplete="off", spellCheck=False, n_submit=0, className="gs-input"),
                        # the selected glacier, laid out like a search result (name, RGI ID); hidden while editing
                        html.Div(className="gs-display", **{"aria-hidden": "true"}, children=[
                            html.Span(id="glacier_display_name", className="gs-name"),
                            html.Span(id="glacier_display_id", className="gs-id"),
                        ]),
                        html.Button("×", id="glacier_clear", n_clicks=0, className="gs-clear is-hidden",
                                    title="Show all glaciers", **{"aria-label": "Show all glaciers"}),
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
                html.H1(className="map-title", title="Back to all glaciers", children=[
                    "Alpine glaciers in ", html.Span(str(config.DEFAULT_YEAR), id="year_label", className="year-label")]),
            ]),

            # theme switch; hidden, map3d.js shows it as a map control under the zoom buttons
            html.Button("☀", id="theme_toggle", n_clicks=0, className="theme-btn",
                        title="Switch light/dark theme", **{"aria-label": "Switch light/dark theme"}),

            # bottom column, stacked from the bottom up: dock, first-visit hint.
            # The column lets clicks through to the map; only its cards take them.
            html.Div(className="bottom-stack", children=[
                # first-visit hint; map3d.js shows it unless the visitor closed it before
                html.Div(id="intro", className="float intro is-hidden", role="note", children=[
                    html.P([
                        html.Strong("380 Alpine glaciers, modelled from 2000 to 2100"),
                        " under three greenhouse gas scenarios. Press ▶ to watch them change, click a glacier "
                        "for its numbers, or click the colour bar to show flow speed, mass balance or temperature.",
                    ]),
                    html.Button("Got it", id="intro_close", className="btn"),
                ]),

                # the temporal choices in one line: play, slider, scenario
                html.Div(className="float dock", children=[
                    html.Div(className="dock-view", children=[
                        # scenario switch like the colour bar: shows the chosen scenario and the selection's volume in
                        # the displayed year; its menu (all scenarios) opens above it
                        html.Div(className="sc-wrap", children=[
                            html.Div(id="sc_menu", className="sc-menu is-hidden", role="listbox",
                                     **{"aria-label": "RCP scenario"}, children=[
                                html.Button(id={"type": "sc_opt", "index": key}, n_clicks=0, className="sc-opt",
                                            children=[
                                    html.Span(label, className="sc-opt-label"),
                                    html.Span(id={"type": "sc_val", "index": key}, className="sc-opt-value"),
                                ]) for key, label in config.SCENARIO_LABELS.items()
                            ]),
                            html.Button(id="sc_btn", n_clicks=0, className="sc-btn",
                                        title="Click to choose the greenhouse gas scenario"),
                        ]),
                    ]),
                    html.Div(className="dock-time", children=[
                        html.Button(PLAY_LABEL, id="btn_timelapse", n_clicks=0, className="btn play-btn",
                                    title="Play or pause the years", **{"aria-label": "Play or pause the years"}),
                        html.Div(className="year", children=dcc.Slider(
                            id="year_slider", min=min(config.YEARS), max=max(config.YEARS), step=1,
                            value=config.DEFAULT_YEAR,
                            marks={y: str(y) for y in range(min(config.YEARS), max(config.YEARS) + 1, 25)},
                            allow_direct_input=False, updatemode="drag")),
                    ]),
                ]),
            ]),

            html.Div(className="legal-corner", children=legal.legal_links()),
            html.Div(className="logo-corner", children=[
                html.Img(src=app.get_asset_url(config.LOGO_FAU), className="logo-fau",
                         alt="Friedrich-Alexander-Universität Erlangen-Nürnberg"),
                html.Img(src=app.get_asset_url(config.LOGO_ERC), className="logo-erc logo-erc-dark",
                         alt="Funded by the European Union · European Research Council"),
                html.Img(src=app.get_asset_url(config.LOGO_ERC_LIGHT), className="logo-erc logo-erc-light",
                         alt="Funded by the European Union · European Research Council"),
            ]),
        ],
    )
