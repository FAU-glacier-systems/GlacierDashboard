# Gunicorn config for the glacier dashboard (used by glacierdash.service).

bind = "127.0.0.1:8050"          # only nginx talks to the app
workers = 2                       # each worker keeps its own frame cache (~200-350 MB)
threads = 4                       # netCDF access is serialized by a lock inside the app
worker_class = "gthread"
timeout = 60
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically to bound memory growth
max_requests = 2000
max_requests_jitter = 200

accesslog = "-"                   # -> journald
errorlog = "-"
loglevel = "info"
forwarded_allow_ips = "127.0.0.1"
control_socket_disable = True     # not used; avoids writing to $HOME under ProtectHome
