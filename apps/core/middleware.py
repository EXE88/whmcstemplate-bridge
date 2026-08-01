import logging
import time

from .logging import set_request_id

logger = logging.getLogger("apps.core.access")

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"


class RequestIDMiddleware:
    """Accept an inbound X-Request-ID (from the edge) or mint one, echo it back."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Client-supplied and echoed into every log line, so strip anything that
        # could forge a new record (newlines) or break a log parser.
        incoming = "".join(
            ch for ch in request.META.get(REQUEST_ID_HEADER, "")[:64] if ch.isalnum() or ch in "-_"
        )
        rid = set_request_id(incoming or None)
        request.request_id = rid
        response = self.get_response(request)
        response["X-Request-ID"] = rid
        return response


class AccessLogMiddleware:
    """One structured access line per request - no bodies, no tokens."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
        )
        response["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
        return response
