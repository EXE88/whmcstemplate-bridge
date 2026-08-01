"""
The one object the rest of the project uses to reach WHMCS.

    from apps.whmcs.client import get_client
    data = get_client().call(Action.GET_INVOICES, {"clientid": 42})

Everything below this line knows about HTTP; everything above it does not.
"""

import logging
from typing import Any

from .actions import ALLOWED_ACTIONS, FORBIDDEN_ACTIONS, Action
from .config import WhmcsSettings, get_settings
from .exceptions import WhmcsConfigError
from .transport import AsyncTransport, SyncTransport

logger = logging.getLogger(__name__)


def _check_action(action: str | Action) -> str:
    name = str(action)
    if name in FORBIDDEN_ACTIONS:
        raise WhmcsConfigError(f"WHMCS action {name!r} is explicitly forbidden.")
    if name not in ALLOWED_ACTIONS:
        raise WhmcsConfigError(
            f"WHMCS action {name!r} is not in the allowlist (apps/whmcs/actions.py)."
        )
    return name


class WhmcsClient:
    """Blocking client. Thread-safe: the underlying httpx client is pooled."""

    def __init__(self, config: WhmcsSettings | None = None):
        self.config = config or get_settings()
        self._transport = SyncTransport(self.config)

    def call(self, action: str | Action, params: dict[str, Any] | None = None) -> dict:
        return self._transport.request(_check_action(action), params or {})


class AsyncWhmcsClient:
    """Async client, for fan-out reads and future WebSocket consumers."""

    def __init__(self, config: WhmcsSettings | None = None):
        self.config = config or get_settings()
        self._transport = AsyncTransport(self.config)

    async def call(self, action: str | Action, params: dict[str, Any] | None = None) -> dict:
        return await self._transport.request(_check_action(action), params or {})


_client: WhmcsClient | None = None
_async_client: AsyncWhmcsClient | None = None


def get_client() -> WhmcsClient:
    global _client
    if _client is None:
        _client = WhmcsClient()
    return _client


def get_async_client() -> AsyncWhmcsClient:
    global _async_client
    if _async_client is None:
        _async_client = AsyncWhmcsClient()
    return _async_client


def reset_clients() -> None:
    """Drop the cached clients - used by tests and after a settings override."""
    global _client, _async_client
    _client = None
    _async_client = None
    SyncTransport.close()
