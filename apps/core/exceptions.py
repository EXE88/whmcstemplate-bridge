"""
A single error contract for the whole API.

Every error - DRF validation, upstream WHMCS failure, unexpected crash - reaches
the SPA as:

    {"error": {"code": "...", "message": "...", "details": {...},
               "request_id": "..."}}

Upstream WHMCS error strings are mapped to stable codes, and *never* leaked raw
when they might contain internal details.
"""

import logging

from django.core.exceptions import PermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.whmcs.exceptions import (
    WhmcsAPIError,
    WhmcsAuthError,
    WhmcsNotFound,
    WhmcsRateLimited,
    WhmcsTransportError,
    WhmcsValidationError,
)

from .logging import get_request_id

logger = logging.getLogger(__name__)


class ApplicationError(Exception):
    """Domain-level failure raised by service functions."""

    default_code = "application_error"
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = details or {}


class ResourceNotFound(ApplicationError):
    default_code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


class ResourceForbidden(ApplicationError):
    default_code = "forbidden"
    status_code = status.HTTP_403_FORBIDDEN


class UpstreamUnavailable(ApplicationError):
    default_code = "upstream_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


def _envelope(code: str, message: str, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": get_request_id(),
        }
    }


# WHMCS exception -> (http status, public code, public message)
_WHMCS_MAP = {
    WhmcsAuthError: (
        status.HTTP_502_BAD_GATEWAY,
        "upstream_auth_failed",
        "The billing system rejected our credentials.",
    ),
    WhmcsNotFound: (status.HTTP_404_NOT_FOUND, "not_found", "Resource not found."),
    WhmcsValidationError: (status.HTTP_400_BAD_REQUEST, "invalid_request", None),
    WhmcsRateLimited: (
        status.HTTP_429_TOO_MANY_REQUESTS,
        "upstream_rate_limited",
        "The billing system is busy, try again shortly.",
    ),
    WhmcsTransportError: (
        status.HTTP_504_GATEWAY_TIMEOUT,
        "upstream_unreachable",
        "The billing system did not respond in time.",
    ),
}


def api_exception_handler(exc, context):
    request_path = getattr(context.get("request"), "path", "?")

    if isinstance(exc, ApplicationError):
        return Response(
            _envelope(exc.code, exc.message, exc.details), status=exc.status_code
        )

    for exc_type, (http_status, code, public_message) in _WHMCS_MAP.items():
        if isinstance(exc, exc_type):
            # Only WHMCS *validation* messages are safe to forward verbatim -
            # they are user-facing ("Invalid Client ID"). Everything else is
            # replaced by a generic message and logged in full.
            message = public_message or str(exc)
            logger.warning("WHMCS error on %s: %s", request_path, exc, exc_info=False)
            return Response(_envelope(code, message), status=http_status)

    if isinstance(exc, WhmcsAPIError):
        logger.error("Unmapped WHMCS error on %s: %s", request_path, exc)
        return Response(
            _envelope("upstream_error", "The billing system returned an error."),
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if isinstance(exc, Http404):
        return Response(_envelope("not_found", "Resource not found."), status=404)

    if isinstance(exc, PermissionDenied):
        return Response(_envelope("forbidden", "Permission denied."), status=403)

    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled exception on %s", request_path)
        return Response(
            _envelope("internal_error", "Unexpected server error."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data
    code = "invalid_request"
    message = "The request could not be processed."
    details: dict = {}

    if isinstance(detail, dict) and "detail" in detail:
        message = str(detail["detail"])
        code = getattr(detail["detail"], "code", None) or code
    elif isinstance(detail, dict):
        message = "Validation failed."
        code = "validation_error"
        details = {
            key: [str(m) for m in value] if isinstance(value, list) else str(value)
            for key, value in detail.items()
        }
    elif isinstance(detail, list):
        message = "Validation failed."
        code = "validation_error"
        details = {"non_field_errors": [str(m) for m in detail]}

    response.data = _envelope(code, message, details)
    return response
