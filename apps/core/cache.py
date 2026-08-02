"""
Cache-aside helpers.

Design rules:

* every key is namespaced by *owner* (``client:42:invoices:...``) so a cached
  payload can never be served to a different customer;
* TTLs come from ``settings.CACHE_TTL`` - tune per environment, 0 disables;
* every namespace carries a version counter, so a write can invalidate a whole
  family of keys in one Redis op instead of scanning for patterns;
* entries are served *stale-while-revalidate*: an expired value is returned
  immediately and refreshed in the background. A WHMCS call costs ~1.1s of PHP
  on the upstream server, so the difference between "expired" and "missing"
  is the difference between a 20ms response and a 1.2s one.
"""

import atexit
import hashlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections

logger = logging.getLogger(__name__)

_VERSION_TTL = 60 * 60 * 24 * 7

#: How long an entry physically survives past its freshness deadline. Within
#: this window it can still be served stale while a refresh runs.
STALE_MULTIPLIER = 10

#: Background refreshes are few and purely I/O bound, so a small pool is plenty.
_refresher = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cache-refresh")
atexit.register(_refresher.shutdown, wait=False)


def ttl_for(bucket: str) -> int:
    return int(settings.CACHE_TTL.get(bucket, 0))


def _version_key(namespace: str) -> str:
    return f"ver:{namespace}"


def namespace_version(namespace: str) -> int:
    version = cache.get(_version_key(namespace))
    if version is None:
        version = 1
        cache.set(_version_key(namespace), version, _VERSION_TTL)
    return int(version)


def invalidate(namespace: str) -> None:
    """Bump the namespace version - every key built from it becomes unreachable."""
    try:
        cache.incr(_version_key(namespace))
    except ValueError:
        # The counter expired or was evicted while payload keys may still be
        # alive at v1. Restarting at 1 would leave those readable, so skip past
        # it - the whole point of this call is that nothing older survives.
        cache.set(_version_key(namespace), 2, _VERSION_TTL)
    logger.debug("cache namespace invalidated: %s", namespace)


def build_key(namespace: str, *parts: Any, **kwargs: Any) -> str:
    payload = json.dumps([parts, sorted(kwargs.items())], default=str, sort_keys=True)
    # Not a security boundary - just a short, stable key from the arguments.
    digest = hashlib.sha1(payload.encode(), usedforsecurity=False).hexdigest()[:16]
    return f"{namespace}:v{namespace_version(namespace)}:{digest}"


def get_or_set(
    namespace: str,
    bucket: str,
    producer: Callable[[], Any],
    *parts: Any,
    stale_ok: bool = True,
) -> Any:
    """
    Cache-aside read for per-customer data, stale-while-revalidate.

        get_or_set(client_namespace(cid, "invoices"), "invoices",
                   lambda: fetch(), page, status)

    Three outcomes:

    * **fresh hit** - returned immediately;
    * **stale hit** - returned immediately *and* a background refresh starts, so
      exactly one request pays for the upstream call and nobody waits for it;
    * **miss** - the caller produces the value and waits.

    Pass ``stale_ok=False`` where a stale answer would be wrong (money about to
    be charged, a value the customer just changed).

    A later write calls ``invalidate(client_namespace(cid, "invoices"))`` and
    only *that* customer's cached entries disappear.
    """
    ttl = ttl_for(bucket)
    if ttl <= 0:
        return producer()

    key = build_key(namespace, *parts)
    entry = cache.get(key)
    now = time.time()

    if isinstance(entry, tuple) and len(entry) == 2:
        value, fresh_until = entry
        if now < fresh_until:
            return value
        if stale_ok:
            _schedule_refresh(key, producer, ttl)
            return value

    value = producer()
    if value is not None:
        _store(key, value, ttl)
    return value


def _store(key: str, value: Any, ttl: int) -> None:
    cache.set(key, (value, time.time() + ttl), ttl * STALE_MULTIPLIER)


def _schedule_refresh(key: str, producer: Callable[[], Any], ttl: int) -> None:
    """Kick off one refresh for this key; concurrent readers keep the stale value."""
    lock_key = f"{key}:refreshing"
    if not cache.add(lock_key, 1, min(ttl, 60)):
        return  # somebody else is already on it

    def run() -> None:
        try:
            value = producer()
            if value is not None:
                _store(key, value, ttl)
        except Exception:
            # A failed refresh is survivable: the stale value stays readable
            # until it falls out of the cache entirely.
            logger.warning("background cache refresh failed for %s", key, exc_info=True)
        finally:
            cache.delete(lock_key)
            close_old_connections()

    try:
        _refresher.submit(run)
    except RuntimeError:  # interpreter shutting down
        cache.delete(lock_key)


def client_namespace(whmcs_client_id: int, resource: str) -> str:
    """Per-customer namespace: ``client:42:invoices``."""
    return f"client:{int(whmcs_client_id)}:{resource}"
