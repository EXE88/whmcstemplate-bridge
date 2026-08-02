# Architecture

## 1. The shape of the system

```
  Next.js / SPA          Django bridge (this repo)              WHMCS
 ┌──────────────┐  JWT  ┌───────────────────────────┐  HTTPS   ┌──────────┐
 │  storefront  │──────>│ DRF views (validation)    │  POST    │ api.php  │
 │  client area │<──────│   ↓                       │─────────>│ (admin   │
 └──────────────┘  JSON │ services (business rules) │<─────────│  creds)  │
                        │   ↓                       │  JSON    └──────────┘
                        │ WhmcsClient → transport   │
                        └───────────────────────────┘
                              │            │
                            Redis        Celery
                           (cache)      (heavy work)
```

Rules that must not be broken:

1. **The frontend never talks to WHMCS.** It has no credentials, does not know
   the WHMCS hostname, and every upstream error is rewritten before it is
   returned (`apps/core/exceptions.py`).
2. **Only `apps/whmcs/transport.py` speaks HTTP.** Nothing above it imports
   `httpx`, so the upstream can be mocked, swapped or wrapped in a circuit
   breaker in one place.
3. **Views never call the client directly.** They validate input, hand
   primitives to a service, and render the result.
4. **Every upstream call is scoped by `request.user.whmcs_client_id`**, taken
   from the authenticated user row - never from the URL or body.

## 2. Layers

| Layer | Location | Responsibility | Knows about |
|---|---|---|---|
| HTTP API | `apps/*/views.py`, `urls.py` | routing, auth, throttling, status codes | serializers, services |
| Validation | `apps/*/serializers.py` | shape/type/length/whitelist of input | nothing upstream |
| Domain | `apps/whmcs/services/*.py` | business rules, ownership, caching, invalidation | client, normalizers |
| Client | `apps/whmcs/client.py` | action allowlist, singleton clients | transport |
| Transport | `apps/whmcs/transport.py` | POST body, retries, timeouts, error mapping | httpx |
| Config | `apps/whmcs/config.py` | endpoint URL and credentials from `.env` | settings |

## 3. Request lifecycle

`GET /api/v1/billing/invoices/?status=Unpaid&page=2`

1. `RequestIDMiddleware` assigns a correlation id (echoed as `X-Request-ID`).
2. `JWTAuthentication` resolves the user; `IsLinkedToWhmcsClient` requires a
   linked `whmcs_client_id`.
3. Throttles: `SustainedUserThrottle` (per user), `WriteRateThrottle` (writes).
4. `InvoiceQuerySerializer` validates `status` against a fixed choice list -
   an arbitrary string can never reach a WHMCS parameter.
5. `BillingService.list_invoices` builds the cache key
   `client:42:invoices:v3:<hash>`; on a miss it calls `GetInvoices` with
   `limitstart`/`limitnum`.
6. The response is unwrapped (`invoices.invoice[]`), fields are projected onto
   our own names, money becomes a decimal string.
7. `paginated_response` returns `{count, page, page_size, num_pages, results}`.

## 4. Errors

Every failure leaves the API in one shape:

```json
{"error": {"code": "upstream_unreachable", "message": "...", "details": {}, "request_id": "9f2c1a"}}
```

| Upstream condition | HTTP | code |
|---|---|---|
| credentials rejected / IP not whitelisted | 502 | `upstream_auth_failed` |
| record does not exist | 404 | `not_found` |
| parameters rejected by WHMCS | 400 | `invalid_request` |
| WHMCS throttling | 429 | `upstream_rate_limited` |
| timeout, TLS error, HTML response | 504 | `upstream_unreachable` |

Only WHMCS *validation* messages are forwarded verbatim; everything else is
replaced with a generic sentence and logged in full server-side.

## 5. Security

| Concern | Mechanism |
|---|---|
| Credential exposure | Only `config.py` reads them; `redact()` masks them in logs; `LOG_WHMCS_PAYLOADS` is off by default |
| Direct WHMCS access | The SPA never receives the WHMCS URL; WHMCS should additionally whitelist only the bridge's IP |
| IDOR | `OwnedResourceMixin.assert_owned` re-checks the owner of every single-record read and returns 404, not 403 |
| Dangerous actions | `apps/whmcs/actions.py` is an allowlist; `FORBIDDEN_ACTIONS` hard-blocks destructive calls |
| Brute force | `LoginRateThrottle` keys on IP **and** submitted email |
| Injection into WHMCS params | Serializers whitelist fields; `EDITABLE_PROFILE_FIELDS` maps our names to WHMCS names explicitly |
| Stored XSS in tickets | `bleach` strips all markup before the message is sent upstream |
| Card data | Never touches the bridge - checkout redirects to WHMCS' hosted invoice page |
| Token theft | 15-minute access tokens, rotating refresh tokens with blacklist on logout |
| Password storage | Customer passwords are never stored locally (`set_unusable_password`); auth round-trips to `ValidateLogin` |

## 5b. Checkout

```
GET  /hosting/products/            browse
GET  /hosting/domains/lookup/      availability
GET  /orders/payment-methods/      gateways enabled in WHMCS
POST /orders/                      AddOrder -> {order_id, invoice_id, payment_url}
     ->  redirect the browser to payment_url (WHMCS hosted invoice page)
     ->  WHMCS takes the payment and provisions per its own automation
GET  /orders/<id>/                 status
```

Rules this flow is built on:

* **The basket carries no prices.** `priceoverride`, `domainpriceoverride`,
  `promooverride`, `affid` and `noinvoice` exist in the WHMCS API and would let
  a caller name their own price; no serializer accepts them and the service
  hardcodes the invoice flags. There is a test that submits them and asserts
  they never reach WHMCS.
* **The bridge never provisions.** `AddOrder` creates a pending order and an
  invoice; `AcceptOrder` is not called from any customer path.
* **No card data.** Payment continues on WHMCS' hosted invoice page, so the
  gateway form and any 3-D Secure step stay on their side.
* **`Idempotency-Key`.** A double-clicked or retried POST with the same key
  returns the original order instead of buying twice; a concurrent duplicate
  gets `order_in_progress`. Keys are namespaced per customer.
* **Basket lines map to WHMCS' parallel arrays** (`pid[0]` is priced with
  `billingcycle[0]`, a domain on the same line uses `domaintype[0]`). Indices
  are emitted explicitly so a product-only line and a domain-only line cannot
  slide into each other.
* Cancellation is allowed only while the order is `Pending`.

## 5b-2. Payment (native Zibal, no WHMCS page)

The customer never sees the WHMCS template. The bridge drives the gateway
itself and tells WHMCS afterwards that the invoice is paid.

```
POST /billing/invoices/34/pay/          -> {redirect_url, payment_id, amount_rial}
  bridge: re-reads the invoice from WHMCS (amount comes from there, never from
          the request), writes a PaymentAttempt row, calls Zibal /v1/request
browser -> https://gateway.zibal.ir/start/<trackId>        (card entry, on Zibal)
Zibal   -> GET /payments/callback/zibal/?trackId=&success=&status=&orderId=
  bridge: POST Zibal /v1/verify  <- the ONLY thing that decides the outcome
          amount check -> AddInvoicePayment in WHMCS -> status RECORDED
        -> 302 to PAYMENT_RESULT_URL?status=success&invoice=34&payment=<uuid>
GET /payments/<uuid>/                   -> the storefront confirms for itself
```

Card data still never touches the bridge - Zibal hosts the card form, as
Shaparak requires. What disappears is the detour through `viewinvoice.php`.

| Risk | Control |
|---|---|
| Forged callback (`?success=1` typed by hand) | the query string only selects *which* attempt to verify; `/v1/verify` server-to-server decides everything |
| Paying someone else's invoice | the invoice is read through `BillingService.get_invoice`, which enforces ownership before an attempt row exists |
| Client dictating the amount | amount is derived from the invoice balance in WHMCS; request body fields named `amount` are ignored |
| Replayed callback crediting twice | unique `track_id`, `select_for_update`, status guard, and WHMCS rejects a duplicate `transid` |
| Paid for less than the invoice | verified amount is compared to the requested amount; a mismatch parks the row as `MISMATCH` and never credits |
| Open redirect via the callback | the result URL is built only from settings plus our own row |
| Money taken but WHMCS unreachable | the row stays `PAID` (never `FAILED`) so it can be reconciled; the customer sees "pending", not an error |
| Toman/rial confusion | `PAYMENT_AMOUNT_MULTIPLIER` is explicit configuration, not a guess |

`AddInvoicePayment` is the one WHMCS action that turns a record into money
received. It is reachable from exactly one place - `BillingService.record_payment`,
called only after a successful verify - and from no serializer or view.

Rows that end up `PAID` but not `RECORDED` are the reconciliation queue: the
customer was charged and WHMCS does not know yet. Surface them in the admin
(`/admin/payments/paymentattempt/?status__exact=paid`).

## 5c. Attachments

Upload is `multipart/form-data` on ticket creation and replies; WHMCS wants the
files as *base64(json([{name, data: base64(bytes)}]))*, which is built in
`apps/whmcs/attachments.py`.

| Risk | Control |
|---|---|
| Executable or renderable uploads (`.php`, `.html`, `.svg`, `.js`) | `FORBIDDEN_EXTENSIONS` - blocked even if the allowlist is widened |
| Path traversal / header injection via filename | `safe_filename` strips directories, separators and control characters |
| Storage exhaustion | 5 files, 5MB each, 15MB per message (all env-tunable), plus Django's own upload ceilings |
| Reading another customer's files | download re-reads the ticket, then requires `related_id` to be that ticket or one of *its* replies |
| Staff-only notes | `type=note` is rejected; only `ticket` and `reply` exist in the serializer |
| Stored XSS from a downloaded file | always `Content-Disposition: attachment`, `application/octet-stream`, `nosniff`, sandbox CSP |

## 5d. Upgrades

```
GET  /hosting/services/<id>/upgrade-options/   plans in the same product group
POST /hosting/services/<id>/upgrade/quote/     calconly - pro-rata price, no order
POST /hosting/services/<id>/upgrade/           order + invoice + payment_url
```

The upgrade target must appear in `upgrade-options`, i.e. it must be in the same
product group as the service's current plan. WHMCS keeps its configured upgrade
paths out of the API, so the group is the closest safe approximation; without
it, a customer could point the upgrade at an unrelated or internal product and
have WHMCS bill them for that instead. Cross-group moves are a support action.

## 6. Caching

Cache-aside, namespaced per owner, invalidated by version bump:

```
client:42:invoices:v3:<hash-of-args>     TTL 45s
catalogue:products:v1:<hash>             TTL 900s
```

`invalidate(client_namespace(42, "invoices"))` increments the namespace version,
which retires every key derived from it in one operation - no key scanning.

Recommended TTLs (all in `.env`):

| Data | Bucket | TTL | Why |
|---|---|---|---|
| Products, TLD pricing, currencies, departments | `catalogue` | 15 min | identical for all visitors, changes rarely |
| Profile, services, domains | `client` | 60 s | changes only when the customer acts |
| Invoices, transactions | `invoices` | 45 s | payment status must feel live |
| Tickets | `tickets` | 30 s | conversation view, invalidated on every reply |

Never cache: login, EPP codes, payment URLs, anything with a one-time value.

## 6b. Performance: the upstream is the budget

Measured against the production WHMCS (8.12.1):

```
DNS lookup                 255 ms   (once)
TCP connect                 59 ms
TLS handshake               97 ms   -> 156 ms per NEW connection
WhmcsDetails (warm conn)  1108 ms
GetProducts               1318 ms
GetCurrencies             1126 ms
median warm call          1147 ms
same call, fresh conn/req 5006 ms   -> keep-alive saves ~3.9 s
```

Network round trip is 59ms; the other ~1000ms is PHP executing inside WHMCS.
Nothing in this codebase can make a WHMCS call fast - so the design goal is to
make **fewer** of them.

Four mechanisms, in order of how much they buy:

1. **Don't call at all.** Cache-aside with per-owner namespaces. A warm read is
   ~0ms locally, ~2ms over Redis, against 1100ms upstream.
2. **Serve stale, refresh behind.** An expired entry is returned immediately and
   refreshed in a background thread, with a lock so only one refresher runs.
   Expiry therefore costs one *background* call, not a 1.1s stall for whoever
   happened to arrive first. Pass `stale_ok=False` where staleness would be
   wrong - invoice balances about to be charged do.
3. **Collapse N+1.** Every avoidable second read was removed: a ticket reply is
   2 upstream calls instead of 3, a nameserver update 3 instead of 4, the credit
   balance reuses the cached profile instead of re-fetching the client record.
   Tests assert the call counts so they cannot creep back.
4. **Fan out what remains.** `apps/core/aggregate.gather` runs independent reads
   in threads. Measured on 5 real uncached calls: **8050ms sequential → 3695ms
   (2.2x)**. `/api/v1/dashboard/` uses it, and returns partial data with an
   `unavailable` list rather than failing whole if one section breaks.

Keep-alive matters more than it looks: the client is pooled per process, so
gunicorn workers must be long-lived (they are) and `preload_app` is on.

**In production, `REDIS_URL` is not optional.** Without it the cache is
per-process locmem, so with 3 gunicorn workers a customer hits a cold cache
roughly two times in three, and background refreshes are duplicated per worker.

If a WHMCS call is still ~1s after all of this, the remaining fix is on the
WHMCS box, not here: PHP opcache, MySQL slow queries, and disabled/slow addon
modules are the usual causes.

The bridge is ASGI-first and ships both a sync and an async transport.

* **Keep sync**: single-call endpoints (invoice list, ticket detail). The
  overhead of async buys nothing when there is one upstream round trip.
* **Make async**: fan-out reads. A dashboard needs profile + services + domains
  + unpaid invoices + open tickets - five sequential calls at ~200 ms each is
  1 s, `asyncio.gather` makes it ~250 ms. Use `get_async_client()` in an
  `async def` view.
* **Move to Celery**: anything that can take longer than a request should
  (`apps/whmcs/tasks.py`) - placing orders, domain registration/transfer,
  package upgrades, bulk syncs, cache warming. The view returns `202` with a
  task id.

## 8. Extension points

**WebSocket** - `config/asgi.py` already exposes the ASGI app; add Channels,
wrap it in a `ProtocolTypeRouter`, and push ticket replies / provisioning
progress from the Celery task that already knows about them.

**Redis** - set `REDIS_URL`; the cache backend and throttle counters switch over
with no code change.

**Celery** - set `CELERY_BROKER_URL`; tasks stop running eagerly. Queues
`provisioning` and `sync` are already routed.

**A second upstream** (e.g. a control panel API) - add a sibling package next to
`apps/whmcs/` with the same transport/client/service split; the API apps stay
unchanged.

**Webhooks from WHMCS** - add `apps/webhooks/` with an HMAC-verified endpoint
that calls `refresh_client_cache.delay(client_id)`; this removes most of the
need for short TTLs.

## 9. Endpoints

| Method | Path | Auth |
|---|---|---|
| POST | `/api/v1/auth/login/` | public |
| POST | `/api/v1/auth/register/` | public |
| POST | `/api/v1/auth/refresh/` `logout/` | token |
| GET/PATCH | `/api/v1/auth/me/` | JWT |
| POST | `/api/v1/auth/me/password/` | JWT |
| GET | `/api/v1/billing/invoices/[<id>/]` | JWT |
| GET | `/api/v1/billing/transactions/` `credit/` `pay-methods/` | JWT |
| GET/POST | `/api/v1/support/tickets/` | JWT |
| GET/DELETE | `/api/v1/support/tickets/<id>/` | JWT |
| POST | `/api/v1/support/tickets/<id>/replies/` | JWT |
| GET | `/api/v1/support/tickets/<id>/attachment/` | JWT |
| GET | `/api/v1/hosting/services/<id>/upgrade-options/` | JWT |
| POST | `/api/v1/hosting/services/<id>/upgrade/quote/` `upgrade/` | JWT |
| GET | `/api/v1/hosting/products/` `tld-pricing/` `domains/lookup/` | public |
| GET | `/api/v1/orders/payment-methods/` | public |
| POST | `/api/v1/billing/invoices/<id>/pay/` | JWT |
| GET | `/api/v1/payments/` `<uuid>/` | JWT |
| GET | `/api/v1/payments/callback/zibal/` | public (gateway) |
| GET/POST | `/api/v1/orders/` | JWT |
| GET | `/api/v1/orders/<id>/` | JWT |
| POST | `/api/v1/orders/<id>/cancel/` | JWT |
| GET | `/api/v1/hosting/services/[<id>/]` | JWT |
| POST | `/api/v1/hosting/services/<id>/password/` `cancellation/` | JWT |
| GET | `/api/v1/hosting/domains/[<id>/]` | JWT |
| GET/PUT | `/api/v1/hosting/domains/<id>/nameservers/` `lock/` | JWT |
| POST | `/api/v1/hosting/domains/<id>/epp/` | JWT |
| GET | `/api/v1/health/` `ready/` | public |
| GET | `/api/docs/` | public (disable in production) |
