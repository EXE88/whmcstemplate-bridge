from django.http import HttpResponse
from django.utils.encoding import escape_uri_path
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response

from apps.core.views_mixins import ClientScopedAPIView
from apps.whmcs.services import SupportService

from .serializers import (
    AttachmentQuerySerializer,
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
            files=read_uploads(data.get("attachments")),
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
            files=read_uploads(serializer.validated_data.get("attachments")),
        )
        return Response(ticket, status=status.HTTP_201_CREATED)


class TicketAttachmentView(ClientScopedAPIView):
    """
    Download one attachment belonging to one of the caller's tickets.

    The bytes are streamed back through the bridge rather than linking to
    WHMCS, so the ownership check cannot be skipped. The response is always a
    download - never rendered inline - because an uploaded file served from our
    own origin would otherwise be a stored-XSS vector.
    """

    @extend_schema(parameters=[AttachmentQuerySerializer], responses={200: bytes})
    def get(self, request, ticket_id: int):
        query = AttachmentQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        filename, content = SupportService().get_attachment(
            self.whmcs_client_id,
            ticket_id,
            kind=data["type"],
            related_id=data["related_id"],
            index=data["index"],
        )

        response = HttpResponse(content, content_type="application/octet-stream")
        response["Content-Disposition"] = f'attachment; filename="{escape_uri_path(filename)}"'
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response


def read_uploads(files) -> list[tuple[str, bytes]]:
    """Materialise uploaded files as ``(name, bytes)`` for the service layer."""
    return [(upload.name, upload.read()) for upload in files or []]
