# Glacier Dashboard: Alps (RGI region 11)

Interactive 3D map of glacier evolution (2000–2100) for 380 glaciers in the European Alps,
under the RCP 2.6 / 4.5 / 8.5 scenarios, with volume and area over time.

Live at https://www.glacier-evolution.nat.fau.de. This branch is what runs on that server.

## Layout

| Path | Contents |
|---|---|
| `code/glacier_dashboard_3d.py` | the app that is served: full-window 3D map, exposes `server` for gunicorn |
| `code/dash3d/` | its parts: glacier list and settings, glacier store, terrain tiles, colour scales, data API, layout, callbacks |
| `code/assets3d/` | its map code (`map3d.js`), stylesheet and the self-hosted MapLibre GL JS |
| `code/legal.py` | Impressum, Datenschutz, Barrierefreiheit |
| `code/assets/` | logos, favicon, base CSS |
| `deploy/` | systemd unit, gunicorn and nginx config, and how to operate the server ([deploy/README.md](deploy/README.md)) |
| `tools/build_glacier_store.py` | builds the read-optimised data store the 3D app reads |
| `tools/compress_netcdf.py` | lossless NetCDF recompression (about 5× smaller, bit-exact verified) |
| `data/glacier_location_and_name/` | glacier names, countries, coordinates |

Not in git (too large, kept on the server):

- `data/glacier_model_data/`: per glacier `<RGI-ID>_Projection_CORDEX_output_2D.nc` (2020–2100, 3 scenarios)
  and `<RGI-ID>_Projection_output_W5E5_const.nc` (2000–2020), about 27 GB compressed
- `data/glacier_store/`: built from those files by `tools/build_glacier_store.py` (about 7 GB, 5 minutes).
  Per glacier, cropped to the glacier: bedrock once, ice thickness per scenario and year, and the other
  properties quantised to one byte, all uncompressed and memory-mapped. `series.npz` caches volume and area.

## How the 3D app works

- **Terrain**: open AWS Terrain Tiles, cleaned of their spikes (bad pixels along seams in the source data), with
  the model bedrock merged in around every glacier. It does not
  change over time, so tiles are built once and cached (`~/.cache/glacier3d`, then nginx).
- **Ice**: `map3d.js` draws every glacier in view as its own 3D surface (bedrock + thickness) in a WebGL
  layer, coloured by the chosen property. A year step is one request (`/api3d/frames`) for all glaciers
  in view, a few tens of KB.
- Year, scenario and property changes are handled in the browser; the server only sends data.

## New model output

1. Recompress and copy the NetCDF files into `data/glacier_model_data/` (`tools/compress_netcdf.py`).
2. Add the glaciers to `data/glacier_location_and_name/glaciers_region11_alps.csv`.
3. Rebuild the store: delete `data/glacier_store/` (or the changed glaciers' folders and `series.npz`), then
   `python tools/build_glacier_store.py`.
4. Delete the terrain tile cache (`~/.cache/glacier3d/terrain_*`, `/var/cache/nginx/tiles`) if bedrock changed,
   and restart the service (see deploy/README.md).

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python tools/build_glacier_store.py              # once, needs the NetCDF files
cd code && DASH_PORT=8060 python glacier_dashboard_3d.py      # http://127.0.0.1:8060
```

## Links to a view

The address bar always reflects the current view, so it can be bookmarked or shared, e.g.
`/?glacier=RGI2000-v7.0-G-11-01522&scenario=rcp_8_5&property=thk&year=2060&metric=volume`.
`glacier=all` shows all glaciers; `property` is one of `thk`, `velsurf_mag`, `smb`, `mean_temp`;
`metric` is `volume` or `area`. `view=lon,lat,zoom,bearing,pitch` is the camera; without it the map flies
to the glacier.

The link preview (Mastodon, Slack, messengers) uses `code/assets/preview.jpg` (1200 × 630) and the page
description in `code/dash3d/layout.py`; set `SITE_URL` if the site moves to another address.
