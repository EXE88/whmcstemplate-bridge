from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response

from apps.core.views_mixins import ClientScopedAPIView
from apps.whmcs.services import SupportService

from .serializers import (
    TicketCreateSerializer,
    TicketQuerySerializer,
    TicketReplySerializer,
    TicketSerializer,
)


class DepartmentListView(ClientScopedAPIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(SupportService().list_departments())


class TicketListCreateView(ClientScopedAPIView):
    @extend_schema(parameters=[TicketQuerySerializer], responses={200: TicketSerializer})
    def get(self, request):
        query = TicketQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        items, total = SupportService().list_tickets(
            self.whmcs_client_id, self.page(), query.validated_data.get("status")
        )
        return self.paginated(items, total)

    @extend_schema(request=TicketCreateSerializer, responses={201: dict})
    def post(self, request):
        serializer = TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ticket = SupportService().open_ticket(
            self.whmcs_client_id,
            department_id=data["department_id"],
            subject=data["subject"],
            message=data["message"],
            priority=data["priority"],
            service_id=data.get("service_id"),
            client_ip=self.client_ip,
        )
        return Response(ticket, status=status.HTTP_201_CREATED)


class TicketDetailView(ClientScopedAPIView):
    @extend_schema(responses={200: TicketSerializer})
    def get(self, request, ticket_id: int):
        return Response(SupportService().get_ticket(self.whmcs_client_id, ticket_id))

    @extend_schema(responses={200: TicketSerializer})
    def delete(self, request, ticket_id: int):
        """Closing, not deleting - customers may never destroy a ticket."""
        return Response(SupportService().close_ticket(self.whmcs_client_id, ticket_id))


class TicketReplyView(ClientScopedAPIView):
    @extend_schema(request=TicketReplySerializer, responses={201: TicketSerializer})
    def post(self, request, ticket_id: int):
        serializer = TicketReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = SupportService().reply_to_ticket(
            self.whmcs_client_id,
            ticket_id,
            serializer.validated_data["message"],
            client_ip=self.client_ip,
        )
        return Response(ticket, status=status.HTTP_201_CREATED)
