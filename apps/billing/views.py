from drf_spectacular.utils import extend_schema
from rest_framework.response import Response

from apps.core.views_mixins import ClientScopedAPIView
from apps.whmcs.services import BillingService

from .serializers import InvoiceQuerySerializer, InvoiceSerializer


class InvoiceListView(ClientScopedAPIView):
    @extend_schema(parameters=[InvoiceQuerySerializer], responses={200: InvoiceSerializer})
    def get(self, request):
        query = InvoiceQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        items, total = BillingService().list_invoices(
            self.whmcs_client_id, self.page(), query.validated_data.get("status")
        )
        return self.paginated(items, total)


class InvoiceDetailView(ClientScopedAPIView):
    @extend_schema(responses={200: InvoiceSerializer})
    def get(self, request, invoice_id: int):
        service = BillingService()
        invoice = service.get_invoice(self.whmcs_client_id, invoice_id)
        if invoice["status"] in ("Unpaid", "Overdue"):
            # Payment happens on WHMCS' hosted page - no card data here, ever.
            invoice["payment_url"] = service.invoice_payment_url(invoice_id)
        return Response(invoice)


class TransactionListView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        items, total = BillingService().list_transactions(self.whmcs_client_id, self.page())
        return self.paginated(items, total)


class CreditBalanceView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(BillingService().get_credit_balance(self.whmcs_client_id))


class PayMethodListView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(BillingService().list_pay_methods(self.whmcs_client_id))
