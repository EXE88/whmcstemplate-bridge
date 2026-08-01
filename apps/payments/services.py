"""
Payment orchestration.

Two operations, both narrow on purpose:

``start`` - the customer asks to pay invoice N. We re-read the invoice from
WHMCS (never trusting an amount from the browser), open a session with the
gateway and hand back a redirect URL.

``settle`` - the gateway sends the customer back. We verify server-to-server,
compare the verified amount against what we asked for, and only then tell WHMCS
the invoice is paid. Everything here is written so that running it twice on the
same trackId cannot credit an invoice twice.
"""

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.exceptions import ApplicationError, ResourceNotFound
from apps.whmcs.services import BillingService

from .gateways import ZibalError, ZibalGateway
from .models import PaymentAttempt, PaymentStatus

logger = logging.getLogger(__name__)

PAYABLE_STATUSES = ("Unpaid", "Overdue")


def _config() -> dict:
    return getattr(settings, "PAYMENTS", {})


def to_rial(amount_whmcs: str) -> int:
    """
    Convert a WHMCS amount into integer rial.

    WHMCS' "IRR" is configured as rial on some installs and as toman on others;
    guessing would be a factor-of-ten error in real money, so the multiplier is
    explicit configuration (``PAYMENT_AMOUNT_MULTIPLIER``).
    """
    multiplier = Decimal(str(_config().get("AMOUNT_MULTIPLIER", 1)))
    return int((Decimal(str(amount_whmcs)) * multiplier).quantize(Decimal("1")))


class PaymentService:
    def __init__(self, billing: BillingService | None = None, gateway=None):
        self.billing = billing or BillingService()
        self._gateway = gateway

    @property
    def gateway(self) -> ZibalGateway:
        if self._gateway is None:
            self._gateway = ZibalGateway()
        return self._gateway

    # -- start ------------------------------------------------------------

    def start(self, user, invoice_id: int, *, client_ip: str = "", mobile: str = "") -> dict:
        whmcs_client_id = int(user.whmcs_client_id)

        # Ownership and amount both come from WHMCS, not from the request.
        invoice = self.billing.get_invoice(whmcs_client_id, invoice_id)
        if invoice["status"] not in PAYABLE_STATUSES:
            raise ApplicationError(
                "This invoice is not payable.",
                code="invoice_not_payable",
                details={"status": invoice["status"]},
            )

        amount_rial = to_rial(invoice["balance"])
        if amount_rial <= 0:
            raise ApplicationError("This invoice has nothing left to pay.", code="nothing_due")

        attempt = PaymentAttempt.objects.create(
            user=user,
            whmcs_client_id=whmcs_client_id,
            invoice_id=invoice_id,
            gateway=self.gateway.module,
            amount_rial=amount_rial,
            amount_whmcs=str(invoice["balance"]),
            client_ip=client_ip or None,
        )

        try:
            track_id = self.gateway.request_payment(
                amount_rial=amount_rial,
                callback_url=self.callback_url(),
                order_id=str(attempt.id),
                description=f"Invoice #{invoice_id}",
                mobile=mobile,
            )
        except ZibalError as exc:
            attempt.status = PaymentStatus.FAILED
            attempt.gateway_result = exc.result
            attempt.gateway_message = exc.message[:255]
            attempt.save(
                update_fields=["status", "gateway_result", "gateway_message", "updated_at"]
            )
            logger.warning("payment request failed for invoice %s: %s", invoice_id, exc)
            raise ApplicationError(
                "The payment gateway is not accepting payments right now.",
                code="gateway_unavailable",
            ) from exc

        attempt.track_id = track_id
        attempt.status = PaymentStatus.REDIRECTED
        attempt.save(update_fields=["track_id", "status", "updated_at"])

        logger.info(
            "payment started: client=%s invoice=%s attempt=%s track=%s amount=%s rial",
            whmcs_client_id,
            invoice_id,
            attempt.id,
            track_id,
            amount_rial,
        )
        return {
            "payment_id": str(attempt.id),
            "redirect_url": self.gateway.start_url(track_id),
            "amount_rial": amount_rial,
            "invoice_id": invoice_id,
        }

    # -- settle -----------------------------------------------------------

    def settle(self, track_id: str) -> PaymentAttempt:
        """
        Verify a returning payment and, if it is good, record it in WHMCS.

        Callable from the browser callback and from a reconciliation job; both
        converge on the same result because the decision comes from the gateway
        verify call and the row is locked while it is applied.
        """
        try:
            attempt = PaymentAttempt.objects.get(track_id=track_id)
        except PaymentAttempt.DoesNotExist:
            logger.warning("callback for unknown track id %s", track_id)
            raise ResourceNotFound("Unknown payment.") from None

        if attempt.is_settled:
            return attempt  # Replayed callback: nothing left to do.

        verification = self.gateway.verify(track_id)

        with transaction.atomic():
            attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            if attempt.is_settled:
                return attempt

            attempt.gateway_result = verification.result
            attempt.gateway_message = verification.status_message[:255]
            attempt.ref_number = verification.ref_number
            attempt.card_mask = verification.card_number
            attempt.paid_at = verification.paid_at or timezone.now()

            if not verification.paid:
                attempt.status = PaymentStatus.FAILED
                attempt.save()
                return attempt

            # A verified payment for the wrong amount is not a payment. Park it
            # for a human rather than crediting the invoice.
            if verification.amount_rial != attempt.amount_rial:
                attempt.status = PaymentStatus.MISMATCH
                attempt.save()
                logger.error(
                    "amount mismatch on %s: asked %s rial, gateway verified %s",
                    attempt.id,
                    attempt.amount_rial,
                    verification.amount_rial,
                )
                return attempt

            attempt.status = PaymentStatus.PAID
            attempt.save()

        # Outside the lock: a slow WHMCS call must not hold a row transaction.
        try:
            self.billing.record_payment(
                attempt.whmcs_client_id,
                attempt.invoice_id,
                transaction_id=attempt.ref_number or track_id,
                gateway=attempt.gateway,
                amount=attempt.amount_whmcs,
                paid_at=attempt.paid_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:
            # The customer paid. If WHMCS cannot be told right now, keep the
            # row as PAID so reconciliation retries it - never as FAILED.
            logger.exception(
                "payment %s verified but not recorded in WHMCS (invoice %s)",
                attempt.id,
                attempt.invoice_id,
            )
            raise

        attempt.status = PaymentStatus.RECORDED
        attempt.recorded_at = timezone.now()
        attempt.save(update_fields=["status", "recorded_at", "updated_at"])
        return attempt

    # -- urls -------------------------------------------------------------

    def callback_url(self) -> str:
        base = str(_config().get("CALLBACK_BASE_URL", "")).rstrip("/")
        if not base:
            raise ApplicationError(
                "PAYMENT_CALLBACK_BASE_URL is not configured.", code="misconfigured"
            )
        return base + reverse("v1:payments:zibal-callback")

    @staticmethod
    def result_url(attempt: PaymentAttempt | None, outcome: str) -> str:
        """
        Where the browser is sent afterwards.

        Built only from configuration and our own row - never from anything in
        the callback request, so this cannot become an open redirect.
        """
        base = str(_config().get("RESULT_URL", "")).rstrip("/")
        if not base:
            return "/"
        params = [f"status={outcome}"]
        if attempt is not None:
            params.append(f"invoice={attempt.invoice_id}")
            params.append(f"payment={attempt.id}")
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{'&'.join(params)}"


OUTCOME_BY_STATUS = {
    PaymentStatus.RECORDED: "success",
    PaymentStatus.PAID: "pending",
    PaymentStatus.MISMATCH: "mismatch",
    PaymentStatus.FAILED: "failed",
    PaymentStatus.CANCELLED: "cancelled",
}
