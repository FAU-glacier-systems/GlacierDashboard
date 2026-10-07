"""
Alps glacier dashboard (3D): what www.glacier-evolution.nat.fau.de serves (see deploy/README.md).

A full-window MapLibre GL map:
  - terrain: open AWS Terrain Tiles with the model bedrock (topg) merged in around every glacier. It does not
    change over time, so it is built once and cached on disk.
  - ice: every glacier in view is drawn by map3d.js as its own 3D surface (bedrock + thickness of the chosen
    scenario and year), coloured by the chosen property. A year step fetches one small binary block with the
    thickness and property of all glaciers in view (/api3d/frames).
  - the colour bar, glacier search and time controls float over the map.

Data come from the read-optimised store built by tools/build_glacier_store.py (memory-mapped numpy arrays).
This file only puts the app together; the parts live in the dash3d package (see dash3d/__init__.py).

Run locally:
    DASH_PORT=8060 python glacier_dashboard_3d.py
"""
import os
import threading

from dash import Dash

import legal
from dash3d import callbacks, clientside, config  # noqa: F401  (callbacks, clientside: register the callbacks)
from dash3d.api import MAPLIBRE, bp as api_blueprint
from dash3d.layout import META_TAGS, PAGE_TITLE, make_layout
from dash3d.terrain import warm_dem

app = Dash(
    __name__,
    assets_folder=str(config.CODE_DIR / "assets"),  # logos, favicon and base stylesheet
    title=PAGE_TITLE,
    meta_tags=META_TAGS,
    index_string=config.INDEX_STRING,
    # MapLibre is served from here (assets3d/vendor), so visitors' browsers contact no third party
    external_stylesheets=[f"/static3d/{MAPLIBRE}/maplibre-gl.css", "/static3d/style3d.css"],
    external_scripts=[f"/static3d/{MAPLIBRE}/maplibre-gl.js", "/static3d/map3d.js"],
)
server = app.server
legal.register(server, app.get_asset_url("style.css"))
server.register_blueprint(api_blueprint)
app.layout = make_layout(app)

# Dash sets itself up on the first request and marks that done before it has finished: a request that arrives in
# parallel (gunicorn runs threads) meanwhile finds no scripts or callbacks registered and fails. Set up now instead.
with server.test_request_context("/"):
    app._setup_server()

threading.Thread(target=warm_dem, daemon=True).start()

if __name__ == "__main__":
    app.run(host=os.environ.get("DASH_HOST", "127.0.0.1"), port=int(os.environ.get("DASH_PORT", "8060")),
            debug=False, threaded=True)
