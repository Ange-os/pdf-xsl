# Configuración Gunicorn para PDF2XLS
# Uso: gunicorn -c gunicorn.conf.py app:app

import os

# Enlace: 0.0.0.0 para escuchar en todas las interfaces; usa 127.0.0.1 si nginx hace proxy
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8090")
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"
worker_connections = 1000
timeout = 300  # conversiones largas pueden tardar
keepalive = 2

# Logs
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")   # "-" = stdout
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Proceso
daemon = False  # systemd gestiona el proceso
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Seguridad / rendimiento
limit_request_line = 4096
limit_request_fields = 100
max_requests = 1000
max_requests_jitter = 50

def on_starting(server):
    """Se ejecuta al arrancar Gunicorn."""
    pass

def when_ready(server):
    """Se ejecuta cuando los workers están listos."""
    pass
