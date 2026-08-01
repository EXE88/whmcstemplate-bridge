from django.urls import path

from .views import (
    OrderCancelView,
    OrderDetailView,
    OrderListCreateView,
    PaymentMethodListView,
)

app_name = "orders"

urlpatterns = [
    path("payment-methods/", PaymentMethodListView.as_view(), name="payment-methods"),
    path("", OrderListCreateView.as_view(), name="order-list"),
    path("<int:order_id>/", OrderDetailView.as_view(), name="order-detail"),
    path("<int:order_id>/cancel/", OrderCancelView.as_view(), name="order-cancel"),
]
