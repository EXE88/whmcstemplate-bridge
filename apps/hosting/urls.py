from django.urls import path

from .views import (
    DomainDetailView,
    DomainEppView,
    DomainListView,
    DomainLockView,
    DomainLookupView,
    DomainNameserverView,
    ProductListView,
    ServiceCancellationView,
    ServiceDetailView,
    ServiceListView,
    ServicePasswordView,
    ServiceUpgradeOptionsView,
    ServiceUpgradeQuoteView,
    ServiceUpgradeView,
    TldPricingView,
)

app_name = "hosting"

urlpatterns = [
    # public catalogue
    path("products/", ProductListView.as_view(), name="product-list"),
    path("tld-pricing/", TldPricingView.as_view(), name="tld-pricing"),
    path("domains/lookup/", DomainLookupView.as_view(), name="domain-lookup"),
    # customer services
    path("services/", ServiceListView.as_view(), name="service-list"),
    path("services/<int:service_id>/", ServiceDetailView.as_view(), name="service-detail"),
    path(
        "services/<int:service_id>/password/",
        ServicePasswordView.as_view(),
        name="service-password",
    ),
    path(
        "services/<int:service_id>/cancellation/",
        ServiceCancellationView.as_view(),
        name="service-cancellation",
    ),
    path(
        "services/<int:service_id>/upgrade-options/",
        ServiceUpgradeOptionsView.as_view(),
        name="service-upgrade-options",
    ),
    path(
        "services/<int:service_id>/upgrade/quote/",
        ServiceUpgradeQuoteView.as_view(),
        name="service-upgrade-quote",
    ),
    path(
        "services/<int:service_id>/upgrade/",
        ServiceUpgradeView.as_view(),
        name="service-upgrade",
    ),
    # customer domains
    path("domains/", DomainListView.as_view(), name="domain-list"),
    path("domains/<int:domain_id>/", DomainDetailView.as_view(), name="domain-detail"),
    path(
        "domains/<int:domain_id>/nameservers/",
        DomainNameserverView.as_view(),
        name="domain-nameservers",
    ),
    path("domains/<int:domain_id>/lock/", DomainLockView.as_view(), name="domain-lock"),
    path("domains/<int:domain_id>/epp/", DomainEppView.as_view(), name="domain-epp"),
]
