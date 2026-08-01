from rest_framework import serializers

from apps.whmcs.services.billing import INVOICE_STATUSES


class InvoiceQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=sorted(INVOICE_STATUSES), required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, max_value=100, required=False)


class InvoiceItemSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    type = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    amount = serializers.CharField(read_only=True)


class InvoiceSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    date = serializers.CharField(read_only=True)
    due_date = serializers.CharField(read_only=True)
    date_paid = serializers.CharField(read_only=True)
    subtotal = serializers.CharField(read_only=True)
    tax = serializers.CharField(read_only=True)
    credit = serializers.CharField(read_only=True)
    total = serializers.CharField(read_only=True)
    currency_code = serializers.CharField(read_only=True)
    payment_method = serializers.CharField(read_only=True)
    items = InvoiceItemSerializer(many=True, read_only=True)
