"""One colour scale per property for all glaciers, and its lookup table for map3d.js."""
import numpy as np
import plotly.colors as pc

from . import config

from .store import GLACIERS

# largest |SMB| the store can hold (its quantisation range, which the data reach): the SMB scale is symmetric
_smb_off, _smb_step = next(iter(GLACIERS.values())).quant["smb"]
SMB_MAX = round(max(abs(_smb_off), abs(_smb_off + 254 * _smb_step)), 1)

# (plotly scale, start of the scale used, min, max, centre).
# A diverging scale puts its middle colour at the centre value, even when the range is not symmetric around it.
VAR_STYLE = {
    "thk": ("Blues", 0.25, 0.0, 300.0, None),
    "velsurf_mag": ("Plasma", 0.0, 0.0, 60.0, None),
    "smb": ("RdBu", 0.0, -SMB_MAX, SMB_MAX, 0.0),
    "mean_temp": ("RdBu_r", 0.0, -8.0, 8.0, 0.0),
}


def colour_stops(var, n):
    """n colours evenly spaced in value from min to max."""
    name, start, lo, hi, centre = VAR_STYLE[var]
    t = np.linspace(0, 1, n)
    if centre is not None:                       # two linear halves meeting in the middle colour at the centre
        v = lo + t * (hi - lo)
        t = np.where(v < centre, 0.5 * (v - lo) / (centre - lo), 0.5 + 0.5 * (v - centre) / (hi - centre))
    return pc.sample_colorscale(pc.get_colorscale(name), list(start + t * (1 - start)))


def var_config(var, lang):
    _, _, lo, hi, _ = VAR_STYLE[var]
    offset, scale = (0.0, 0.1) if var == "thk" else next(iter(GLACIERS.values())).quant[var]
    lut = [int(c) for s in colour_stops(var, 256) for c in pc.unlabel_rgb(s)]
    return {"lo": lo, "hi": hi, "offset": offset, "scale": scale, "lut": lut,
            "label": config.VAR_LABELS[lang].get(var, var)}
