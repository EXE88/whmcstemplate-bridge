from django.urls import path

from .views import DepartmentListView, TicketDetailView, TicketListCreateView, TicketReplyView

app_name = "support"

urlpatterns = [
    path("departments/", DepartmentListView.as_view(), name="department-list"),
    path("tickets/", TicketListCreateView.as_view(), name="ticket-list"),
    path("tickets/<int:ticket_id>/", TicketDetailView.as_view(), name="ticket-detail"),
    path("tickets/<int:ticket_id>/replies/", TicketReplyView.as_view(), name="ticket-reply"),
]
