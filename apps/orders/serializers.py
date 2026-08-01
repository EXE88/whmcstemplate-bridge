"""
Basket validation.

This is the strictest serializer in the project: it is the only user input that
turns into an invoice. Note what is *absent* - there is no price, no discount
amount, no ``priceoverride``, no ``promooverride``, no ``affid`` and no
``noinvoice``. Those exist in the WHMCS API and would let a caller order at a
price of their choosing; they are unreachable by construction.
"""

from rest_framework import serializers

from apps.hosting.serializers import HOSTNAME_RE
from apps.whmcs.services.orders import BILLING_CYCLES, DOMAIN_ACTIONS, MAX_ITEMS

MAX_QUANTITY = 10
MAX_YEARS = 10


class ProductItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["product"])
    product_id = serializers.IntegerField(min_value=1)
    billing_cycle = serializers.ChoiceField(choices=BILLING_CYCLES)
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY, default=1)
    # Domain the service is provisioned for (hosting accounts).
    domain = serializers.CharField(max_length=253, required=False, allow_blank=True)
    hostname = serializers.CharField(max_length=253, required=False, allow_blank=True)
    addons = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, max_length=20
    )
    config_options = serializers.DictField(
        child=serializers.CharField(max_length=100), required=False
    )
    custom_fields = serializers.DictField(
        child=serializers.CharField(max_length=255), required=False
    )

    def validate_domain(self, value: str) -> str:
        return _clean_domain(value) if value else ""

    def validate_config_options(self, value: dict) -> dict:
        return _numeric_keys(value, "config_options")

    def validate_custom_fields(self, value: dict) -> dict:
        return _numeric_keys(value, "custom_fields")


class DomainItemSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["domain"])
    domain = serializers.CharField(max_length=253)
    action = serializers.ChoiceField(choices=sorted(DOMAIN_ACTIONS))
    years = serializers.IntegerField(min_value=1, max_value=MAX_YEARS, default=1)
    epp_code = serializers.CharField(max_length=100, required=False, allow_blank=True)
    id_protection = serializers.BooleanField(default=False)
    dns_management = serializers.BooleanField(default=False)
    email_forwarding = serializers.BooleanField(default=False)

    def validate_domain(self, value: str) -> str:
        return _clean_domain(value)

    def validate(self, attrs: dict) -> dict:
        if attrs["action"] == "transfer" and not attrs.get("epp_code"):
            raise serializers.ValidationError(
                {"epp_code": "An EPP/auth code is required to transfer a domain."}
            )
        if attrs["action"] == "register" and attrs.get("epp_code"):
            attrs.pop("epp_code")
        return attrs


class OrderCreateSerializer(serializers.Serializer):
    items = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=MAX_ITEMS)
    payment_method = serializers.CharField(max_length=50)
    promo_code = serializers.CharField(max_length=50, required=False, allow_blank=True)
    nameservers = serializers.ListField(
        child=serializers.CharField(max_length=253),
        required=False,
        min_length=2,
        max_length=5,
    )

    def validate_items(self, value: list[dict]) -> list[dict]:
        cleaned = []
        for index, raw in enumerate(value):
            item_type = raw.get("type")
            if item_type == "product":
                serializer = ProductItemSerializer(data=raw)
            elif item_type == "domain":
                serializer = DomainItemSerializer(data=raw)
            else:
                raise serializers.ValidationError(
                    {index: "Each item needs a type of 'product' or 'domain'."}
                )
            if not serializer.is_valid():
                raise serializers.ValidationError({index: serializer.errors})
            cleaned.append(dict(serializer.validated_data))

        domains = [item["domain"] for item in cleaned if item["type"] == "domain"]
        if len(set(domains)) != len(domains):
            raise serializers.ValidationError("The same domain appears twice in the basket.")
        return cleaned

    def validate_nameservers(self, value: list[str]) -> list[str]:
        return [_clean_domain(host) for host in value]


class OrderSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    order_number = serializers.CharField(read_only=True)
    invoice_id = serializers.IntegerField(read_only=True)
    date = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    payment_status = serializers.CharField(read_only=True)
    amount = serializers.CharField(read_only=True)
    payment_method = serializers.CharField(read_only=True)
    payment_url = serializers.CharField(read_only=True)


def _clean_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if not HOSTNAME_RE.match(domain):
        raise serializers.ValidationError(f"{value!r} is not a valid domain name.")
    return domain


def _numeric_keys(value: dict, field: str) -> dict:
    """WHMCS keys config options and custom fields by their numeric id."""
    cleaned = {}
    for key, item in value.items():
        if not str(key).isdigit():
            raise serializers.ValidationError(f"{field} keys must be numeric ids.")
        cleaned[int(key)] = item
    return cleaned
