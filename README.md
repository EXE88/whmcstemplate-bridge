# WHMCS Bridge

A Django + DRF interface layer between a headless storefront (Next.js or any
SPA) and a WHMCS installation. The frontend talks only to this service; WHMCS
credentials, hostname and API surface never leave the server.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design, layer rules,
caching strategy and security model, and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
for putting it on a server.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements/dev.txt
cp .env.example .env        # then fill in the WHMCS_* values
python manage.py migrate
python manage.py whmcs_ping # verifies host + credentials
python manage.py runserver
```

API docs: <http://127.0.0.1:8000/api/docs/>

## Connecting to WHMCS (what to fill in)

`.env` is already pointed at `https://lithiumhost.ir` (WHMCS is installed on the
domain root, admin at `/admin`, so the API endpoint is
`https://lithiumhost.ir/includes/api.php`). Two values are still blank because
they must be generated inside WHMCS:

1. Log into the admin area and open
   **System Settings > API Credentials > Generate New API Credential**.
2. Pick the admin account, confirm its role has the **API Access** permission,
   and copy the generated *identifier* and *secret* into `WHMCS_IDENTIFIER` and
   `WHMCS_SECRET`.
3. Add the bridge server's public IP to the API IP whitelist (or set
   `WHMCS_ACCESS_KEY` to the `$api_access_key` value from `configuration.php`).
4. Run `python manage.py whmcs_ping` - it prints the endpoint and confirms the
   credentials are accepted.

**The bridge never needs the admin panel password, the server's SSH login or the
MySQL credentials.** It talks to WHMCS only over the HTTP API, with a credential
that can be revoked on its own without touching the admin account. Putting the
admin password in `.env` would hand an attacker full panel access if the file
ever leaked, so that field is left empty on purpose.

## Configuring the WHMCS connection

Everything about the upstream box lives in `.env` - nothing is hardcoded:

```dotenv
WHMCS_SCHEME=https          # http only allowed in DEBUG
WHMCS_HOST=203.0.113.10     # hostname or bare IP
WHMCS_PORT=8443             # omitted from the URL when it is the scheme default
WHMCS_BASE_PATH=/whmcs      # empty when WHMCS is on the domain root
WHMCS_API_PATH=/includes/api.php
WHMCS_HOST_HEADER=billing.example.com   # when talking to an IP behind a vhost
WHMCS_IDENTIFIER=...        # Admin > System Settings > API Credentials
WHMCS_SECRET=...
WHMCS_ACCESS_KEY=...        # optional: $api_access_key from configuration.php
```

The resulting endpoint is `https://203.0.113.10:8443/whmcs/includes/api.php`.
`manage.py whmcs_ping` prints it and confirms the credentials are accepted.

On the WHMCS side: create API credentials for an admin whose role has the
**API Access** permission, and whitelist this server's IP (or set
`WHMCS_ACCESS_KEY`).

## Layout

```
config/            settings (base/local/production), urls, asgi, celery
apps/core/         cross-cutting: errors, cache, throttles, pagination, logging
apps/whmcs/        the only code that talks to WHMCS
  config.py          endpoint + credentials from .env
  transport.py       httpx, retries, timeouts, error classification
  client.py          action allowlist, singleton sync/async clients
  actions.py         allowlisted + explicitly forbidden WHMCS actions
  normalizers.py     unwrap and type WHMCS' JSON
  services/          business rules, ownership checks, caching
  tasks.py           Celery jobs
apps/accounts/     JWT auth backed by WHMCS ValidateLogin
apps/billing/      invoices, transactions, credit
apps/support/      tickets
apps/hosting/      products, services, domains
apps/orders/       basket, checkout, payment handoff
tests/             transport + API tests with WHMCS mocked at the HTTP layer
```

## Placing an order

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/ \
  -H "Authorization: Bearer $ACCESS" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"payment_method":"zarinpal","items":[{"type":"product","product_id":5,"billing_cycle":"annually","domain":"example.ir"},{"type":"domain","domain":"example.ir","action":"register","years":2}]}'
```

Returns `{order_id, invoice_id, payment_url, service_ids, domain_ids}` - send
the browser to `payment_url` and WHMCS handles the gateway and provisioning.
See "Checkout" in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the rules
this flow enforces.

## Commands

```bash
python manage.py whmcs_ping        # check connectivity and credentials
python manage.py reconcile_payments --dry-run   # payments taken but not yet in WHMCS
pytest                             # run the suite (no network access needed)
ruff check . && black --check .    # lint / format
celery -A config worker -l info    # background jobs (needs CELERY_BROKER_URL)
```

## Adding an endpoint

1. Add the WHMCS action to `apps/whmcs/actions.py`.
2. Add a method to the relevant service - scope it by `whmcs_client_id`, map
   fields explicitly, decide on caching and invalidation.
3. Add a serializer for the input and a thin view extending
   `ClientScopedAPIView`.
4. Route it, then add a test that mocks the WHMCS endpoint with `respx`.

Never import `httpx`, `WhmcsClient` or a WHMCS field name outside
`apps/whmcs/`.
