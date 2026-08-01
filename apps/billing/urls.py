from django.urls import path

from apps.payments.views import InvoicePaymentStartView

from .views import (
    CreditBalanceView,
    InvoiceDetailView,
    InvoiceListView,
    PayMethodListView,
    TransactionListView,
)

app_name = "billing"

urlpatterns = [
    path("invoices/", InvoiceListView.as_view(), name="invoice-list"),
    path("invoices/<int:invoice_id>/", InvoiceDetailView.as_view(), name="invoice-detail"),
    path(
        "invoices/<int:invoice_id>/pay/",
        InvoicePaymentStartView.as_view(),
        name="invoice-pay",
    ),
    path("transactions/", TransactionListView.as_view(), name="transaction-list"),
    path("credit/", CreditBalanceView.as_view(), name="credit"),
    path("pay-methods/", PayMethodListView.as_view(), name="pay-method-list"),
]
