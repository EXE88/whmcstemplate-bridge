import re

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.whmcs.services.orders import BILLING_CYCLES

HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$", re.IGNORECASE)


class ServiceSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    domain = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    billing_cycle = serializers.CharField(read_only=True)
    amount = serializers.CharField(read_only=True)
    next_due_date = serializers.CharField(read_only=True)


class ServicePasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True, min_length=10, max_length=64)

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value


class UpgradeQuoteSerializer(serializers.Serializer):
    new_product_id = serializers.IntegerField(min_value=1)
    billing_cycle = serializers.ChoiceField(choices=BILLING_CYCLES)
    promo_code = serializers.CharField(max_length=50, required=False, allow_blank=True)


class UpgradeSerializer(UpgradeQuoteSerializer):
    payment_method = serializers.CharField(max_length=50)


class CancellationSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
    immediate = serializers.BooleanField(default=False)


class NameserverSerializer(serializers.Serializer):
    nameservers = serializers.ListField(
        child=serializers.CharField(max_length=253),
        min_length=2,
        max_length=5,
    )

    def validate_nameservers(self, value: list[str]) -> list[str]:
        cleaned = []
        for host in value:
            host = host.strip().lower().rstrip(".")
            if not HOSTNAME_RE.match(host):
                raise serializers.ValidationError(f"{host!r} is not a valid hostname.")
            cleaned.append(host)
        if len(set(cleaned)) != len(cleaned):
            raise serializers.ValidationError("Nameservers must be distinct.")
        return cleaned


class DomainLockSerializer(serializers.Serializer):
    locked = serializers.BooleanField()


class DomainLookupSerializer(serializers.Serializer):
    domain = serializers.CharField(max_length=253)

    def validate_domain(self, value: str) -> str:
        domain = value.strip().lower().rstrip(".")
        if not HOSTNAME_RE.match(domain):
            raise serializers.ValidationError("Enter a valid domain name.")
        return domain
