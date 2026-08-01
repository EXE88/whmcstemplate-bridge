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

## 7. What to make async, and when

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
