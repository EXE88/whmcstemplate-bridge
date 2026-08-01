"""
HTTP transport for the WHMCS External API.

Responsibilities (and nothing else):

* build the POST body: ``action`` + params + credentials + ``responsetype=json``
* one pooled httpx client per process, sync and async flavours
* retries with backoff on transport errors and 5xx - but only for idempotent
  actions, so a failed ``AddOrder`` is never silently duplicated
* translate transport/protocol failures into ``apps.whmcs.exceptions``
* keep credentials out of every log line

Note on the wire format: WHMCS' API is a single endpoint that takes
``application/x-www-form-urlencoded`` POST bodies and returns JSON when
``responsetype=json`` is set. Nested values are flattened by the caller.
"""

import logging
import random
import time
from typing import Any

import httpx

from .config import WhmcsSettings, get_settings, redact
from .exceptions import (
    WhmcsAPIError,
    WhmcsAuthError,
    WhmcsNotFound,
    WhmcsRateLimited,
    WhmcsTransportError,
    WhmcsValidationError,
)

logger = logging.getLogger(__name__)

#: Actions that may safely be retried after a network failure.
IDEMPOTENT_PREFIXES = ("Get", "List", "Validate", "Domain Whois", "Whmcs")

RETRYABLE_STATUS = frozenset({502, 503, 504, 522, 524})

#: Substrings of WHMCS error messages -> our exception classes. WHMCS has no
#: machine-readable error codes, so message matching is unavoidable; keep it
#: narrow and lowercase.
_ERROR_SIGNATURES: tuple[tuple[str, type[WhmcsAPIError]], ...] = (
    ("invalid ip", WhmcsAuthError),
    ("authentication failed", WhmcsAuthError),
    ("invalid credential", WhmcsAuthError),
    ("invalid api credential", WhmcsAuthError),
    ("access denied", WhmcsAuthError),
    ("api access", WhmcsAuthError),
    ("not found", WhmcsNotFound),
    ("does not exist", WhmcsNotFound),
    ("no results", WhmcsNotFound),
    ("rate limit", WhmcsRateLimited),
    ("too many", WhmcsRateLimited),
)


def _classify(message: str, action: str, payload: dict) -> WhmcsAPIError:
    lowered = message.lower()
    for signature, exc_class in _ERROR_SIGNATURES:
        if signature in lowered:
            return exc_class(message, action=action, payload=payload)
    # Everything else is a rejected-parameters style error.
    return WhmcsValidationError(message, action=action, payload=payload)


def _is_idempotent(action: str) -> bool:
    return action.startswith(IDEMPOTENT_PREFIXES)


def flatten(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """
    Flatten nested dict/list params into WHMCS' ``a[b][0]`` form-field syntax and
    stringify scalars. ``None`` values are dropped so optional arguments can be
    passed through unconditionally.
    """
    flat: dict[str, str] = {}
    for key, value in params.items():
        field = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, dict):
            flat.update(flatten(value, field))
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    flat.update(flatten(item, f"{field}[{index}]"))
                else:
                    flat[f"{field}[{index}]"] = str(item)
        elif isinstance(value, bool):
            flat[field] = "1" if value else "0"
        else:
            flat[field] = str(value)
    return flat


class BaseTransport:
    def __init__(self, config: WhmcsSettings | None = None):
        self.config = config or get_settings()

    # -- shared helpers ---------------------------------------------------

    def _body(self, action: str, params: dict[str, Any]) -> dict[str, str]:
        body = flatten(params)
        body.update(
            {
                "action": action,
                "responsetype": "json",
            }
        )
        body.update(self.config.credentials())
        return body

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.config.timeout, connect=self.config.connect_timeout)

    def _log_request(self, action: str, body: dict) -> None:
        if self.config.log_payloads:
            logger.debug("WHMCS -> %s %s", action, redact(body))
        else:
            logger.debug("WHMCS -> %s", action)

    def _parse(self, response: httpx.Response, action: str, body: dict) -> dict:
        # WHMCS sends its own error envelope with non-2xx statuses - an IP that
        # is not whitelisted comes back as HTTP 403 with
        # {"result":"error","message":"Invalid IP x.x.x.x"}. So the body is read
        # first: reading the status first would turn a permanent auth failure
        # into a "transport error" and retry it pointlessly.
        data = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = None

        if data is None:
            if response.status_code in RETRYABLE_STATUS:
                raise WhmcsTransportError(
                    f"WHMCS returned HTTP {response.status_code} for {action}"
                )
            if response.status_code == 429:
                raise WhmcsRateLimited("WHMCS is rate limiting the bridge", action=action)
            if response.status_code >= 400:
                raise WhmcsTransportError(
                    f"WHMCS returned HTTP {response.status_code} for {action}"
                )
            # 2xx that is not a JSON object: an HTML page, a WAF challenge or a
            # PHP fatal rendered into the body.
            snippet = response.text[:200].replace("\n", " ")
            raise WhmcsTransportError(
                f"Non-JSON response from WHMCS for {action}: {snippet!r}"
            )

        if response.status_code == 429:
            raise WhmcsRateLimited("WHMCS is rate limiting the bridge", action=action)

        if data.get("result") == "error" or data.get("status") == "error":
            message = str(data.get("message") or data.get("result") or "Unknown WHMCS error")
            logger.info("WHMCS <- %s error: %s", action, message)
            raise _classify(message, action, redact(body))

        if self.config.log_payloads:
            logger.debug("WHMCS <- %s ok: %s", action, data)
        return data

    def _sleep_for(self, attempt: int) -> float:
        # Exponential backoff with jitter, so parallel workers do not resonate.
        return self.config.retry_backoff * (2**attempt) * (0.5 + random.random() / 2)  # noqa: S311


class SyncTransport(BaseTransport):
    """Blocking transport used by DRF views, services and Celery tasks."""

    _client: httpx.Client | None = None

    def client(self) -> httpx.Client:
        if SyncTransport._client is None:
            SyncTransport._client = httpx.Client(
                timeout=self._timeout(),
                verify=self.config.verify,
                headers=self.config.default_headers,
                limits=httpx.Limits(
                    max_connections=self.config.max_connections,
                    max_keepalive_connections=self.config.max_connections // 2 or 1,
                ),
                follow_redirects=False,
            )
        return SyncTransport._client

    def request(self, action: str, params: dict[str, Any] | None = None) -> dict:
        body = self._body(action, params or {})
        self._log_request(action, body)
        attempts = self.config.max_retries + 1 if _is_idempotent(action) else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                response = self.client().post(self.config.endpoint, data=body)
                result = self._parse(response, action, body)
                logger.info(
                    "whmcs.call action=%s ok elapsed_ms=%.1f",
                    action,
                    (time.perf_counter() - started) * 1000,
                )
                return result
            except (httpx.TransportError, WhmcsTransportError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                delay = self._sleep_for(attempt)
                logger.warning(
                    "whmcs.call action=%s attempt=%s failed (%s), retrying in %.2fs",
                    action,
                    attempt + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)

        raise WhmcsTransportError(f"WHMCS call {action} failed: {last_error}") from last_error

    @classmethod
    def close(cls) -> None:
        if cls._client is not None:
            cls._client.close()
            cls._client = None


class AsyncTransport(BaseTransport):
    """
    Async twin of :class:`SyncTransport`.

    Useful for endpoints that fan out to several WHMCS actions at once (a
    dashboard needs client + services + invoices + tickets), and required if the
    project later serves WebSockets from the same ASGI process.
    """

    _client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if AsyncTransport._client is None:
            AsyncTransport._client = httpx.AsyncClient(
                timeout=self._timeout(),
                verify=self.config.verify,
                headers=self.config.default_headers,
                limits=httpx.Limits(
                    max_connections=self.config.max_connections,
                    max_keepalive_connections=self.config.max_connections // 2 or 1,
                ),
                follow_redirects=False,
            )
        return AsyncTransport._client

    async def request(self, action: str, params: dict[str, Any] | None = None) -> dict:
        import asyncio

        body = self._body(action, params or {})
        self._log_request(action, body)
        attempts = self.config.max_retries + 1 if _is_idempotent(action) else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await self.client().post(self.config.endpoint, data=body)
                return self._parse(response, action, body)
            except (httpx.TransportError, WhmcsTransportError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                await asyncio.sleep(self._sleep_for(attempt))

        raise WhmcsTransportError(f"WHMCS call {action} failed: {last_error}") from last_error

    @classmethod
    async def close(cls) -> None:
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None
