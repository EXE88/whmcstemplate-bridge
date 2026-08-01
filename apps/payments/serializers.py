from rest_framework import serializers

from .models import PaymentAttempt


class PaymentStartSerializer(serializers.Serializer):
    # Optional: Zibal pre-fills it on the gateway page.
    mobile = serializers.RegexField(r"^09\d{9}$", required=False, allow_blank=True)


class PaymentAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAttempt
        # No internal gateway codes, no client ip, no user id: the storefront
        # needs the outcome, not the audit trail.
        fields = (
            "id",
            "invoice_id",
            "gateway",
            "amount_rial",
            "amount_whmcs",
            "status",
            "ref_number",
            "card_mask",
            "paid_at",
            "created_at",
        )
        read_only_fields = fields
