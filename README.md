# Glacier Dashboard: Alps (RGI region 11)

Interactive Dash app showing 3D glacier evolution (2000–2100) for glaciers in the European Alps,
under the RCP 2.6 / 4.5 / 8.5 scenarios, plus metrics over time.

Live at https://www.glacier-evolution.nat.fau.de. This branch is what runs on that server.

## Layout

| Path | Contents |
|---|---|
| `code/glacier_dashboard_alps.py` | the Dash app (exposes `server` for gunicorn) |
| `code/assets/` | logos and CSS |
| `deploy/` | systemd unit, gunicorn config, and how to operate the server ([deploy/README.md](deploy/README.md)) |
| `tools/compress_netcdf.py` | lossless NetCDF recompression (about 5× smaller, bit-exact verified) |
| `data/glacier_location_and_name/` | glacier names, countries, coordinates |
| `glacierdash/` | lists of glaciers to add/remove |

Not in git (too large, kept on the server):

- `data/glacier_model_data/`: per glacier `<RGI-ID>_Projection_CORDEX_output_2D.nc` (2020–2100, 3 scenarios)
  and `<RGI-ID>_Projection_output_W5E5_const.nc` (2000–2020), about 10 GB compressed
- `data/metrics_over_time_graphic/glacier_yearly_metrics.csv`: yearly metrics for the time-series plot

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd code && python glacier_dashboard_alps.py      # http://127.0.0.1:8050
```

Set `GLACIER_NC_DIR` if the NetCDF files live somewhere other than `data/glacier_model_data/`.
