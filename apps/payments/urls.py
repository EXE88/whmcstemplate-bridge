from django.urls import path

from .views import PaymentListView, PaymentStatusView, ZibalCallbackView

app_name = "payments"

urlpatterns = [
    path("", PaymentListView.as_view(), name="payment-list"),
    path("<uuid:payment_id>/", PaymentStatusView.as_view(), name="payment-status"),
    # Public: the browser arrives here from the gateway.
    path("callback/zibal/", ZibalCallbackView.as_view(), name="zibal-callback"),
]
