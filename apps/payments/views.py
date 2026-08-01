import logging

from django.shortcuts import redirect
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.throttling import BurstAnonThrottle
from apps.core.views_mixins import ClientScopedAPIView

from .models import PaymentAttempt, PaymentStatus
from .serializers import PaymentAttemptSerializer, PaymentStartSerializer
from .services import OUTCOME_BY_STATUS, PaymentService

logger = logging.getLogger(__name__)


class InvoicePaymentStartView(ClientScopedAPIView):
    """
    Begin paying an invoice.

    Returns the gateway URL to send the browser to. The amount is read from
    WHMCS inside the service - the request body cannot influence it.
    """

    @extend_schema(request=PaymentStartSerializer, responses={201: dict})
    def post(self, request, invoice_id: int):
        serializer = PaymentStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = PaymentService().start(
            request.user,
            invoice_id,
            client_ip=self.client_ip,
            mobile=serializer.validated_data.get("mobile", ""),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class ZibalCallbackView(APIView):
    """
    Where Zibal returns the customer.

    Unauthenticated by necessity: the browser arrives from the gateway with no
    token. Nothing in the query string is trusted - ``trackId`` only selects
    which attempt to verify, and the verify call decides the outcome. The
    response is always a redirect to the storefront, built from configuration.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [BurstAnonThrottle]

    @extend_schema(responses={302: None})
    def get(self, request):
        track_id = str(request.query_params.get("trackId", ""))[:64]
        service = PaymentService()

        if not track_id:
            return redirect(service.result_url(None, "invalid"))

        try:
            attempt = service.settle(track_id)
        except Exception:
            # Either the payment is unknown, or WHMCS could not be updated. The
            # customer must not see a stack trace at the end of a purchase.
            logger.exception("callback handling failed for track %s", track_id)
            attempt = PaymentAttempt.objects.filter(track_id=track_id).first()
            return redirect(service.result_url(attempt, "pending"))

        outcome = OUTCOME_BY_STATUS.get(PaymentStatus(attempt.status), "pending")
        return redirect(service.result_url(attempt, outcome))


class PaymentStatusView(ClientScopedAPIView):
    """Lets the storefront confirm the outcome itself instead of believing the
    query string it was redirected with."""

    @extend_schema(responses={200: PaymentAttemptSerializer})
    def get(self, request, payment_id):
        attempt = PaymentAttempt.objects.filter(
            pk=payment_id, whmcs_client_id=self.whmcs_client_id
        ).first()
        if attempt is None:
            from apps.core.exceptions import ResourceNotFound

            raise ResourceNotFound("Payment not found.")
        return Response(PaymentAttemptSerializer(attempt).data)


class PaymentListView(ClientScopedAPIView):
    @extend_schema(responses={200: PaymentAttemptSerializer})
    def get(self, request):
        page = self.page()
        queryset = PaymentAttempt.objects.filter(whmcs_client_id=self.whmcs_client_id)
        total = queryset.count()
        window = queryset[page.limit_start : page.limit_start + page.limit_num]
        return self.paginated(PaymentAttemptSerializer(window, many=True).data, total)
