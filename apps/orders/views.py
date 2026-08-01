import logging

from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ApplicationError
from apps.core.views_mixins import ClientScopedAPIView
from apps.whmcs.services import OrderService

from .serializers import OrderCreateSerializer, OrderSerializer

logger = logging.getLogger(__name__)

#: How long a completed order is remembered against its idempotency key.
IDEMPOTENCY_TTL = 60 * 60 * 24
#: How long a key stays locked while the WHMCS call is in flight.
IDEMPOTENCY_LOCK_TTL = 120


class PaymentMethodListView(APIView):
    """Gateways the storefront may offer. Public: the checkout page needs them
    before the customer logs in."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "anon"

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(OrderService().list_payment_methods())


class OrderListCreateView(ClientScopedAPIView):
    @extend_schema(responses={200: OrderSerializer})
    def get(self, request):
        items, total = OrderService().list_orders(
            self.whmcs_client_id, self.page(), request.query_params.get("status")
        )
        return self.paginated(items, total)

    @extend_schema(request=OrderCreateSerializer, responses={201: dict})
    def post(self, request):
        """
        Place an order. Returns the order, its invoice and the payment URL.

        Send an ``Idempotency-Key`` header: a retried or double-clicked request
        with the same key returns the original order instead of buying twice.
        """
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        key = self._idempotency_key(request)
        if key:
            previous = cache.get(key)
            if previous is not None:
                logger.info("idempotent replay of order for client %s", self.whmcs_client_id)
                return Response(previous, status=status.HTTP_200_OK)
            if not cache.add(f"{key}:lock", "in-flight", IDEMPOTENCY_LOCK_TTL):
                raise ApplicationError(
                    "An identical order is already being processed.",
                    code="order_in_progress",
                )

        try:
            result = OrderService().create_order(
                self.whmcs_client_id,
                items=data["items"],
                payment_method=data["payment_method"],
                promo_code=data.get("promo_code", ""),
                nameservers=data.get("nameservers"),
                client_ip=self.client_ip,
            )
        except Exception:
            if key:
                # Let the customer retry: nothing was recorded as completed.
                cache.delete(f"{key}:lock")
            raise

        if key:
            cache.set(key, result, IDEMPOTENCY_TTL)

        return Response(result, status=status.HTTP_201_CREATED)

    def _idempotency_key(self, request) -> str | None:
        raw = request.headers.get("Idempotency-Key", "").strip()
        if not raw:
            return None
        token = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")[:64]
        if not token:
            return None
        # Namespaced per customer: one client's key can never return another's
        # order, however guessable the key is.
        return f"idem:order:{self.whmcs_client_id}:{token}"


class OrderDetailView(ClientScopedAPIView):
    @extend_schema(responses={200: OrderSerializer})
    def get(self, request, order_id: int):
        return Response(OrderService().get_order(self.whmcs_client_id, order_id))


class OrderCancelView(ClientScopedAPIView):
    @extend_schema(request=None, responses={200: OrderSerializer})
    def post(self, request, order_id: int):
        return Response(OrderService().cancel_order(self.whmcs_client_id, order_id))
