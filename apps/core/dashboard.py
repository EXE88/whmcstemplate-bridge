"""One request, one screen: everything the client area's landing page needs."""

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response

from apps.core.aggregate import gather
from apps.core.pagination import PageRequest
from apps.core.views_mixins import ClientScopedAPIView
from apps.whmcs.services import (
    AccountService,
    BillingService,
    DomainService,
    ServiceService,
    SupportService,
)

logger = logging.getLogger(__name__)

_PAGE = PageRequest(page=1, page_size=5)


class DashboardView(ClientScopedAPIView):
    """
    Aggregates the five reads the landing page used to make one after another.

    Sequentially that was ~5.5s of WHMCS time; run together it costs about as
    much as the slowest single call, and after the first visit most of it comes
    from cache anyway.
    """

    @extend_schema(responses={200: dict})
    def get(self, request):
        client_id = self.whmcs_client_id

        sections = gather(
            {
                "profile": lambda: AccountService().get_profile(client_id),
                "services": lambda: ServiceService().list_services(client_id, _PAGE, "Active"),
                "domains": lambda: DomainService().list_domains(client_id, _PAGE),
                "invoices": lambda: BillingService().list_invoices(client_id, _PAGE, "Unpaid"),
                "tickets": lambda: SupportService().list_tickets(client_id, _PAGE, "Open"),
            }
        )

        services, service_count = sections["services"] or ([], 0)
        domains, domain_count = sections["domains"] or ([], 0)
        invoices, invoice_count = sections["invoices"] or ([], 0)
        tickets, ticket_count = sections["tickets"] or ([], 0)
        profile = sections["profile"] or {}

        return Response(
            {
                "profile": {
                    "first_name": profile.get("first_name", ""),
                    "last_name": profile.get("last_name", ""),
                    "email": profile.get("email", ""),
                    "credit": profile.get("credit", "0.00"),
                    "currency_code": profile.get("currency_code", ""),
                },
                "summary": {
                    "active_services": service_count,
                    "domains": domain_count,
                    "unpaid_invoices": invoice_count,
                    "open_tickets": ticket_count,
                },
                "services": services,
                "domains": domains,
                "unpaid_invoices": invoices,
                "open_tickets": tickets,
                # Tells the frontend which panels to render as "unavailable"
                # rather than empty.
                "unavailable": [name for name, value in sections.items() if value is None],
            }
        )
