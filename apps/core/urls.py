from django.urls import path

from .dashboard import DashboardView
from .views import HealthView, ReadinessView

app_name = "core"

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("ready/", ReadinessView.as_view(), name="ready"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
]
