"""Flask routes for map3d.js: its static files, terrain tiles, and the bedrock and ice of the glaciers in view."""
import gzip

import numpy as np
from flask import Blueprint, Response, abort, request, send_from_directory

from . import config

from .colours import VAR_STYLE
from .store import GLACIER_IDS, GLACIERS
from .terrain import DEM_MAXZOOM, TERRAIN_VERSION, merged_terrain

ASSETS3D_DIR = config.CODE_DIR / "assets3d"
MAPLIBRE = "vendor/maplibre-gl-5.24.0"

bp = Blueprint("api3d", __name__)


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


@bp.route("/static3d/<path:name>")
def static3d(name):
    return send_from_directory(ASSETS3D_DIR, name, max_age=60)


@bp.route("/api3d/terrain/<version>/<int:z>/<int:x>/<int:y>.png")
def api_terrain(version, z, x, y):
    if version != TERRAIN_VERSION or not (0 <= z <= DEM_MAXZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        abort(404)
    return Response(merged_terrain(z, x, y), mimetype="image/png", headers={"Cache-Control": "public, max-age=604800"})


@bp.route("/api3d/beds")
def api_beds():
    """Bedrock of several glaciers (float32, every stride-th cell), concatenated in the order asked."""
    return _binary([np.ascontiguousarray(GLACIERS[i].topg[::s, ::s], dtype="<f4").tobytes() for i, s in _ids_arg()])


@bp.route("/api3d/frames")
def api_frames():
    """Ice of several glaciers for one scenario/year: per glacier thickness (uint16, dm) and, unless the
    property is the thickness, the quantised property (uint8, padded to an even length)."""
    ids = _ids_arg()
    scenario, var = request.args.get("scenario"), request.args.get("var")
    try:
        year = int(request.args.get("year", ""))
    except ValueError:
        abort(400)
    if scenario not in config.SCENARIO_TO_IDX or var not in VAR_STYLE or not config.YEARS[0] <= year <= config.YEARS[-1]:
        abort(400)
    si, yi = config.SCENARIO_TO_IDX[scenario], year - config.YEARS[0]
    chunks = []
    for i, s in ids:
        g = GLACIERS[i]
        chunks.append(np.ascontiguousarray(g.thk[si, yi, ::s, ::s], dtype="<u2").tobytes())
        if var != "thk":
            p = np.ascontiguousarray(g.prop(var)[si, yi, ::s, ::s]).tobytes()
            chunks.append(p + (b"\0" if len(p) % 2 else b""))
    return _binary(chunks)
