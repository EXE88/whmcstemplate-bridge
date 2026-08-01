"""Exception hierarchy for the WHMCS boundary.

Only this package raises these; `apps.core.exceptions` translates them into HTTP
responses. Nothing above the service layer should import httpx.
"""


class WhmcsError(Exception):
    """Base class for anything that goes wrong talking to WHMCS."""


class WhmcsConfigError(WhmcsError):
    """Missing/invalid local configuration (credentials, host, ...)."""


class WhmcsTransportError(WhmcsError):
    """Network failure, timeout, TLS problem, non-JSON body."""


class WhmcsAPIError(WhmcsError):
    """WHMCS answered with ``result: error``."""

    def __init__(self, message: str, *, action: str = "", payload: dict | None = None):
        super().__init__(message)
        self.message = message
        self.action = action
        self.payload = payload or {}


class WhmcsAuthError(WhmcsAPIError):
    """Our API credentials were rejected, or our IP is not whitelisted."""


class WhmcsNotFound(WhmcsAPIError):
    """The requested WHMCS record does not exist."""


class WhmcsValidationError(WhmcsAPIError):
    """WHMCS rejected the parameters - message is safe to show the user."""


class WhmcsRateLimited(WhmcsAPIError):
    """WHMCS (or a WAF in front of it) is throttling us."""
