# Running the dashboard in production

```
browser ──HTTPS──> nginx (:443, TLS, gzip) ──> gunicorn 127.0.0.1:8050 (2 workers × 4 threads) ──> Dash app
```

- `glacierdash.service`: systemd unit, installed at `/etc/systemd/system/glacierdash.service`.
  Runs as `ubuntu` (not root), restarts automatically, capped at 2 GB RAM, logs go to journald.
- `gunicorn.conf.py`: workers, threads, timeouts, worker recycling.
- nginx site: `/etc/nginx/sites-available/glacierdash` (proxy + gzip for the JSON figure payloads).

## Everyday commands

| Task | Command |
|---|---|
| Deploy code changes | `sudo systemctl restart glacierdash` |
| Status / memory | `systemctl status glacierdash` |
| Live logs | `journalctl -u glacierdash -f` |
| Stop / start | `sudo systemctl stop glacierdash` / `sudo systemctl start glacierdash` |
| After editing the unit file | `sudo cp deploy/glacierdash.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart glacierdash` |
| After editing nginx | `sudo nginx -t && sudo systemctl reload nginx` |

## Local development

```
cd code && /home/ubuntu/dashboard-venv/bin/python glacier_dashboard_alps.py   # http://127.0.0.1:8050
```
Stop the service first (or set `DASH_PORT=8051`), since production already uses port 8050.

## Tunables (environment variables)

- `GLACIER_NC_DIR`: NetCDF folder (default `data/glacier_model_data`).
- `GLACIER_3D_CACHE_SIZE`: cached 3D frames per worker (default 128, about 1 MB each).

Set them in the unit file with `Environment=NAME=value`.

## Storage

`tools/compress_netcdf.py SRC DST` recompresses NetCDF files losslessly (zlib + shuffle,
one chunk per frame) and verifies every file bit-exact. That shrinks them by about 4.5×, and reads are just as fast.
New model output should be run through it before being copied into `data/glacier_model_data`.
