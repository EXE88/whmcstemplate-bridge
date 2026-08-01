# Deployment

Target shape: the bridge runs on its own hostname (`api.lithiumhost.ir`) behind
nginx + TLS, talking to WHMCS over HTTPS. Postgres and Redis are local to the
bridge; **the bridge never touches the WHMCS database.**

```
browser ──TLS──> nginx ──> gunicorn (127.0.0.1:8000) ──HTTPS──> lithiumhost.ir/includes/api.php
                             │
                       postgres + redis
                             │
                        celery worker/beat
```

## Where to run it

**Recommended: a separate VPS (or a separate user/vhost) from WHMCS.** A cPanel
box is built around PHP; running a long-lived Python service there fights the
panel, and a bug in one system should not be able to take down billing.

If it must live on the same server, give it its own subdomain and proxy to
gunicorn on a loopback port - never expose port 8000 publicly. In that case set
`WHMCS_HOST=127.0.0.1` plus `WHMCS_HOST_HEADER=lithiumhost.ir` to skip the
round trip through the public interface.

## 1. Prepare the server

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip \
    postgresql redis-server nginx git certbot python3-certbot-nginx

sudo adduser --system --group --home /srv/whmcs-bridge bridge
sudo mkdir -p /srv/whmcs-bridge/run && sudo chown -R bridge:bridge /srv/whmcs-bridge
```

## 2. Database

```bash
sudo -u postgres psql -c "CREATE USER bridge WITH PASSWORD 'a-long-random-password';"
sudo -u postgres psql -c "CREATE DATABASE bridge OWNER bridge;"
```

Redis needs no configuration beyond binding to localhost (the default). Confirm
with `redis-cli ping`.

## 3. Code and virtualenv

```bash
sudo -u bridge git clone https://github.com/EXE88/whmcstemplate-bridge.git /srv/whmcs-bridge
cd /srv/whmcs-bridge
sudo -u bridge python3.12 -m venv .venv
sudo -u bridge .venv/bin/pip install -r requirements/base.txt
```

## 4. Configuration

```bash
sudo -u bridge cp deploy/env.production.example .env
sudo -u bridge chmod 600 .env
sudo -u bridge .venv/bin/python -c \
  "from django.core.management.utils import get_random_secret_key as k; print(k()); print(k())"
sudo -u bridge nano .env       # paste the two keys, the DB password, WHMCS credentials
```

Two settings decide whether the deployment behaves correctly:

| Setting | Why it matters |
|---|---|
| `NUM_PROXIES=1` | Behind nginx every request arrives from `127.0.0.1`. Without this, `X-Forwarded-For` is ignored, so the per-IP login limiter becomes a single global bucket and the IP reported to WHMCS for fraud checks is meaningless. Set `2` if Cloudflare sits in front. |
| `X-Forwarded-Proto` in nginx | `SECURE_PROXY_SSL_HEADER` reads it. Missing it means Django believes the request is plaintext and `SECURE_SSL_REDIRECT` sends the browser into a redirect loop. |

## 5. Migrate, collect static, verify the upstream

```bash
cd /srv/whmcs-bridge
sudo -u bridge .venv/bin/python manage.py migrate
sudo -u bridge .venv/bin/python manage.py collectstatic --noinput
sudo -u bridge .venv/bin/python manage.py whmcs_ping     # must print OK + WHMCS version
sudo -u bridge .venv/bin/python manage.py createsuperuser  # /admin access only
```

`whmcs_ping` failing with `Invalid IP x.x.x.x` means this server is not in the
WHMCS API whitelist - add it under **Setup › General Settings › Security**, or
set `WHMCS_ACCESS_KEY`. Whitelisting is the safer of the two.

## 6. Services

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now whmcs-bridge whmcs-bridge-worker whmcs-bridge-beat
sudo systemctl status whmcs-bridge
```

## 7. nginx + TLS

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/api.lithiumhost.ir
sudo ln -s /etc/nginx/sites-available/api.lithiumhost.ir /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.lithiumhost.ir
```

Point an A record for `api.lithiumhost.ir` at the server first, or certbot
cannot validate.

## 8. Firewall

```bash
sudo ufw allow 22,80,443/tcp && sudo ufw enable
```

Postgres, Redis and gunicorn must stay on loopback. Verify nothing else is
listening publicly:

```bash
sudo ss -tlnp | grep -v '127.0.0.1\|::1'
```

## 9. Smoke test

```bash
curl -s https://api.lithiumhost.ir/api/v1/health/          # {"status":"ok"}
curl -s https://api.lithiumhost.ir/api/v1/ready/           # database + whmcs both ok
curl -s https://api.lithiumhost.ir/api/v1/hosting/products/ | head -c 300
curl -si https://api.lithiumhost.ir/api/v1/billing/invoices/ | head -n 1   # 401 expected
```

Then log in from the frontend origin and confirm the browser gets CORS headers.

## Updating

```bash
cd /srv/whmcs-bridge
sudo -u bridge git pull
sudo -u bridge .venv/bin/pip install -r requirements/base.txt
sudo -u bridge .venv/bin/python manage.py migrate
sudo -u bridge .venv/bin/python manage.py collectstatic --noinput
sudo systemctl reload whmcs-bridge          # zero-downtime worker reload
sudo systemctl restart whmcs-bridge-worker whmcs-bridge-beat
```

## Docker alternative

`docker-compose.yml` in the repository root runs the same stack (api, worker,
beat, postgres, redis). It is the faster path if the server already runs Docker:

```bash
cp deploy/env.production.example .env && nano .env
docker compose up -d --build
docker compose exec api python manage.py migrate
```

nginx and TLS still terminate in front of it, with the same
`client_max_body_size` and forwarding headers.

## Operations

**Logs** - `journalctl -u whmcs-bridge -f`. Every line carries the request id
that the API also returns in `X-Request-ID` and inside error bodies, so a
customer complaint maps to exact log lines.

**Backups** - only Postgres holds state worth keeping (the email ↔ WHMCS client
id mapping and JWT blacklist); everything else lives in WHMCS.

```bash
sudo -u postgres pg_dump bridge | gzip > /var/backups/bridge-$(date +%F).sql.gz
```

**Scaling** - WHMCS calls take 1-3.5s, so concurrency comes from threads, not
CPU. Raise `GUNICORN_THREADS` before `GUNICORN_WORKERS`, and remember WHMCS
itself is the bottleneck: raise `CACHE_TTL_*` before adding capacity.

**Keys** - `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY` must differ from any
development value. Rotating `JWT_SIGNING_KEY` logs every customer out at once.

## Checklist before going live

- [ ] `DJANGO_DEBUG=False` and `DJANGO_SETTINGS_MODULE=config.settings.production`
- [ ] Fresh `DJANGO_SECRET_KEY` and `JWT_SIGNING_KEY`
- [ ] `NUM_PROXIES` matches the real proxy count
- [ ] `.env` is `chmod 600` and owned by `bridge` (and still git-ignored)
- [ ] `EXPOSE_API_DOCS=False`, `LOG_WHMCS_PAYLOADS=False`
- [ ] `CORS_ALLOWED_ORIGINS` lists only the real storefront origins
- [ ] `whmcs_ping` succeeds from the server itself
- [ ] Postgres/Redis/gunicorn are not reachable from outside
- [ ] TLS certificate installed and auto-renewal (`systemctl status certbot.timer`)
- [ ] A monitor watching `/api/v1/ready/`
