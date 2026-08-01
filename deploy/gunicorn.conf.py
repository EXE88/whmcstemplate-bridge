"""
Gunicorn configuration.

Sizing note: every request that touches WHMCS waits on a remote HTTP call -
measured at 1-3.5s against the production install. That is I/O, not CPU, so
*threads* buy far more concurrency here than processes do. Sync workers would
each sit blocked for seconds at a time and the service would stall under a
handful of concurrent users.
"""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")

workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))

# Must exceed the worst-case upstream wait: WHMCS_TIMEOUT (15s) plus retries
# and backoff. A shorter value kills requests that were about to succeed.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 90))
graceful_timeout = 30
keepalive = 5

# Recycle workers to bound the effect of any slow leak.
max_requests = 1000
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms "%({x-request-id}o)s"'

preload_app = True
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")
