from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.whmcs.actions import Action
from apps.whmcs.client import get_client
from apps.whmcs.exceptions import WhmcsAPIError, WhmcsError

from .throttling import BurstAnonThrottle

_PROBE_KEY = "health:whmcs-reachable"


class HealthView(APIView):
    """Liveness probe - answers without touching WHMCS."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes: list = []

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response({"status": "ok"})


class ReadinessView(APIView):
    """
    Readiness probe - verifies the DB and the WHMCS link.

    Deliberately unauthenticated but detail-free: it never reveals the WHMCS
    host or credentials, only whether each dependency answered.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    # Unauthenticated *and* it touches WHMCS, so it must be throttled: otherwise
    # anyone could turn this endpoint into an amplifier against the billing
    # system (and get our IP blocked by it).
    throttle_classes = [BurstAnonThrottle]

    #: The upstream probe result is reused for this long (seconds).
    PROBE_TTL = 30

    @extend_schema(responses={200: dict})
    def get(self, request):
        checks = {"database": "ok", "whmcs": "ok"}
        healthy = True

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            checks["database"] = "error"
            healthy = False

        if not self._whmcs_reachable():
            checks["whmcs"] = "error"
            healthy = False

        return Response(
            {"status": "ok" if healthy else "degraded", "checks": checks},
            status=200 if healthy else 503,
        )

    def _whmcs_reachable(self) -> bool:
        """One upstream probe per PROBE_TTL, however often we are polled."""
        cached = cache.get(_PROBE_KEY)
        if cached is not None:
            return bool(cached)
        try:
            get_client().call(Action.WHMCS_DETAILS)
            reachable = True
        except (WhmcsAPIError, WhmcsError):
            reachable = False
        cache.set(_PROBE_KEY, reachable, self.PROBE_TTL)
        return reachable
