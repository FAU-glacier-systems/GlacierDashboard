"""Server callbacks: selection, settings from the URL, and the area series of the selection."""
from dash import Input, Output, State, callback, ctx
from dash.exceptions import PreventUpdate

from . import config

from .colours import VAR_STYLE
from .store import SERIES, SERIES_INDEX


@callback(
    Output("scenario", "data"), Output("property", "data"),
    Input("url", "search"),
)
def settings_from_url(search):
    q = config.url_params(search)
    scenario = q.get("scenario") if q.get("scenario") in config.SCENARIO_LABELS else config.DEFAULT_SCENARIO
    var = q.get("property") if q.get("property") in VAR_STYLE else config.DEFAULT_VAR
    return scenario, var


@callback(
    Output("selected_rgi", "data"),
    Input("url", "search"), Input("rgi_select", "data"),
    State("selected_rgi", "data"),
)
def select_glacier(search, picked, current_rgi):
    """Single owner of the selection (None = all glaciers). The search and map clicks set rgi_select: {rgi, t}."""
    if ctx.triggered_id == "rgi_select":
        rgi = (picked or {}).get("rgi")
        if rgi == current_rgi or (rgi and rgi not in config.ALL_RGIS):
            raise PreventUpdate
        return rgi
    # no glacier in the URL: the Aletsch glacier; "glacier=all" keeps the all-glaciers view on reload
    rgi = config.url_params(search).get("glacier")
    if rgi == "all":
        return None
    return rgi if rgi in config.ALL_RGIS else config.DEFAULT_RGI


@callback(
    Output("year_slider", "value"),
    Input("url", "search"),
)
def set_year(search):
    try:
        year = int(config.url_params(search).get("year", config.default_year()))
    except ValueError:
        year = config.default_year()
    return year if year in config.YEARS else config.default_year()


@callback(Output("series_data", "data"), Input("selected_rgi", "data"))
def series_data(rgi):
    """Area of the selected glacier (or the sum of all), per scenario and year."""
    arr = SERIES["area"]
    data = arr[SERIES_INDEX[rgi]] if rgi in SERIES_INDEX else arr.sum(axis=0)
    return {"area": [[float(f"{v:.4g}") for v in row] for row in data]}
