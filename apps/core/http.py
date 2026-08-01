"""Request helpers that must not trust the client."""

from django.conf import settings


def client_ip(request) -> str:
    """
    The caller's IP, as far as it can be trusted.

    ``X-Forwarded-For`` is attacker-controlled unless a known number of reverse
    proxies sit in front of us, so it is only consulted when ``NUM_PROXIES`` is
    configured, and then the *n*-th entry from the right is used - the last hop
    the trusted proxy actually observed. Anything a client prepends is ignored.

    This value ends up in WHMCS as ``clientip`` (fraud checks, ticket audit
    trail), so a spoofable value would be worse than none.
    """
    num_proxies = settings.REST_FRAMEWORK.get("NUM_PROXIES")
    remote_addr = request.META.get("REMOTE_ADDR", "")

    if not num_proxies:
        return remote_addr

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not forwarded:
        return remote_addr

    hops = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(hops) < num_proxies:
        return remote_addr
    return hops[-int(num_proxies)]
