from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1 = [
    path("auth/", include("apps.accounts.urls")),
    path("billing/", include("apps.billing.urls")),
    path("support/", include("apps.support.urls")),
    path("hosting/", include("apps.hosting.urls")),
    path("orders/", include("apps.orders.urls")),
    path("", include("apps.core.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include((api_v1, "v1"))),
]

# The schema maps every endpoint and parameter of the API; publishing it to the
# internet only helps someone probing it. Opt in explicitly (EXPOSE_API_DOCS=True)
# if the docs are needed on a deployed environment.
if settings.DEBUG or getattr(settings, "EXPOSE_API_DOCS", False):
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path(
            "api/docs/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
    ]
