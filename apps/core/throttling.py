"""
Throttling.

Three layers, all backed by the configured cache (Redis in production so the
counters are shared across gunicorn workers):

* anon/user  - blanket rates applied to every endpoint
* login      - tight rate on credential endpoints, keyed by IP *and* username
* write      - a lower ceiling for anything that mutates state in WHMCS
"""

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, SimpleRateThrottle


class BurstAnonThrottle(AnonRateThrottle):
    scope = "anon"


class SustainedUserThrottle(ScopedRateThrottle):
    scope_attr = "throttle_scope"
    scope = "user"

    def get_cache_key(self, request, view):
        # Fall back to the global "user" scope when a view declares none.
        self.scope = getattr(view, self.scope_attr, "user")
        if not self.scope:
            return None
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        ident = (
            request.user.pk
            if request.user and request.user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginRateThrottle(SimpleRateThrottle):
    """
    Per (IP, account) bucket: stops one account being brute-forced.

    On its own this does NOT stop credential stuffing - a new email means a new
    bucket - so it must always be paired with :class:`LoginIPThrottle`, which
    caps the attempts from an address regardless of which account they target.
    """

    scope = "login"

    def get_cache_key(self, request, view):
        identity = ""
        if isinstance(getattr(request, "data", None), dict):
            identity = str(request.data.get("email") or request.data.get("username") or "")
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{self.get_ident(request)}:{identity.lower()[:120]}",
        }


class LoginIPThrottle(SimpleRateThrottle):
    """Total credential attempts from one address, whatever account they name."""

    scope = "login_ip"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class WriteRateThrottle(SimpleRateThrottle):
    scope = "write"

    def allow_request(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        ident = (
            request.user.pk
            if request.user and request.user.is_authenticated
            else self.get_ident(request)
        )
        return self.cache_format % {"scope": self.scope, "ident": ident}
