# Running the dashboard in production

```
browser ──HTTPS──> nginx (:443, TLS, gzip, cache) ──> gunicorn 127.0.0.1:8050 (2 workers × 4 threads) ──> glacier_dashboard_3d
```

- `glacierdash.service`: systemd unit, installed at `/etc/systemd/system/glacierdash.service`.
  Runs `glacier_dashboard_3d:server` as `ubuntu` (not root), restarts automatically, capped at 2 GB RAM,
  logs go to journald. The home directory is read-only for it except `~/.cache/glacier3d` (terrain tile cache).
  `LimitNOFILE=8192` because the app memory-maps one file per glacier, more than the default limit of 1024.
- `gunicorn.conf.py`: workers, threads, timeouts, worker recycling.
- nginx site: `/etc/nginx/sites-available/glacierdash` (proxy + gzip for the JSON figure payloads), copy in `nginx-site.conf`.
  It caches the 3D app's data under `/api3d/` (terrain tiles, per-year ice blocks; fixed per URL).
  The cache zone and the rate limit for `/api3d/` (50 requests/s per IP, bursts of 300) are defined in `nginx-tiles.conf`, installed at `/etc/nginx/conf.d/tiles.conf` (cache in `/var/cache/nginx/tiles`, max 2 GB). HTTP/2 is on.
- Logs: the full `access.log` (complete IPs) keeps nginx's default of 14 days. An anonymised copy for visitor statistics (last IPv4 byte / IPv6 after /48 zeroed) goes to `/var/log/nginx/stats/access-anon.log` and is kept one year: format in `nginx-logs.conf` (installed at `/etc/nginx/conf.d/logs.conf`), rotation in `logrotate-nginx-stats` (installed at `/etc/logrotate.d/nginx-stats`). Both retention periods are stated on the privacy page (`code/legal.py`).

## Everyday commands

| Task | Command |
|---|---|
| Test before deploying | `cd code && DASH_PORT=8060 python glacier_dashboard_3d.py &` then `python tools/smoke_test.py` (see its docstring) |
| Deploy code changes | `sudo systemctl restart glacierdash` |
| Status / memory | `systemctl status glacierdash` |
| Live logs | `journalctl -u glacierdash -f` |
| Stop / start | `sudo systemctl stop glacierdash` / `sudo systemctl start glacierdash` |
| After editing the unit file | `sudo cp deploy/glacierdash.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart glacierdash` |
| After editing nginx | `sudo nginx -t && sudo systemctl reload nginx` |

After a restart with new code, open pages show "A new version of this page is available – Reload" (map3d.js
compares the version the page was built with against `/api3d/version`), instead of failing on callbacks that
changed. Pages opened before this check existed still need a manual reload once.

## Local development

```
cd code && DASH_PORT=8060 /home/ubuntu/dashboard-venv/bin/python glacier_dashboard_3d.py   # http://127.0.0.1:8060
```
Production uses port 8050. Memory is tight (3.9 GB, no swap): stop the test app when done.

## Tunables (environment variables)

- `GLACIER_STORE_DIR`: data store of the 3D app (default `data/glacier_store`, see `tools/build_glacier_store.py`).
- `GLACIER3D_CACHE_DIR`: terrain tile cache (default `~/.cache/glacier3d`; must be writable for the service).
- `DEM_TILE_URL`: source of the open terrain tiles (default AWS Terrain Tiles, terrarium encoding).
- `SITE_URL`: public address, used in the link preview tags (default `https://www.glacier-evolution.nat.fau.de`).

Set them in the unit file with `Environment=NAME=value`.

## Storage

`tools/compress_netcdf.py SRC DST` recompresses NetCDF files losslessly (zlib + shuffle,
one chunk per frame) and verifies every file bit-exact. That shrinks them by about 4.5×, and reads are just as fast.
New model output should be run through it before being copied into `data/glacier_model_data`.
