"""
Payment attempts.

The bridge is otherwise stateless - WHMCS owns the data - but a payment cannot
be: between "we sent the customer to the gateway" and "the gateway told us it
was paid" there is state that only we hold, and losing it means either charging
someone with nothing to show for it or crediting an invoice twice.

One row per attempt. The row, not the callback, is the source of truth about
what we asked the gateway for.
"""

import uuid

from django.conf import settings
from django.db import models


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"          # created, customer not sent yet
    REDIRECTED = "redirected", "Redirected"  # customer is at the gateway
    PAID = "paid", "Paid"                    # gateway verified the payment
    RECORDED = "recorded", "Recorded"        # WHMCS invoice marked as paid
    FAILED = "failed", "Failed"              # gateway said no
    CANCELLED = "cancelled", "Cancelled"     # customer walked away
    MISMATCH = "mismatch", "Amount mismatch"  # verified amount != requested


class PaymentAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    whmcs_client_id = models.PositiveIntegerField(db_index=True)
    invoice_id = models.PositiveIntegerField(db_index=True)

    gateway = models.CharField(max_length=32, default="zibal")
    # What we asked the gateway to take, in the gateway's own unit (rial).
    amount_rial = models.PositiveBigIntegerField()
    # The same figure in WHMCS' currency, as the string WHMCS gave us.
    amount_whmcs = models.CharField(max_length=32)

    status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    # Unique: the gateway's reference is what makes a replayed callback a no-op.
    track_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    ref_number = models.CharField(max_length=64, blank=True)
    card_mask = models.CharField(max_length=32, blank=True)

    gateway_result = models.IntegerField(null=True, blank=True)
    gateway_message = models.CharField(max_length=255, blank=True)

    paid_at = models.DateTimeField(null=True, blank=True)
    recorded_at = models.DateTimeField(null=True, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_attempt"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["whmcs_client_id", "invoice_id"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.gateway}:{self.track_id or self.id} invoice={self.invoice_id}"

    @property
    def is_settled(self) -> bool:
        """True once the money is both verified and reflected in WHMCS."""
        return self.status == PaymentStatus.RECORDED
