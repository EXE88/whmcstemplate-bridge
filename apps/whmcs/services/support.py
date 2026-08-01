"""Support tickets and departments."""

import logging

from apps.core.cache import client_namespace, get_or_set
from apps.core.pagination import PageRequest

from ..actions import Action
from ..normalizers import collection, iso, pick, text, to_int, total_of
from .base import BaseService, OwnedResourceMixin

logger = logging.getLogger(__name__)

# GetTickets returns the ticket id as "id"; GetTicket returns it as "ticketid".
# The detail path normalises before projecting, so both land on "id" here.
_TICKET_MAP = {
    "id": ("id", to_int),
    "ticket_number": ("tid", text),
    "department": ("deptname", text),
    "department_id": ("deptid", to_int),
    "subject": ("subject", text),
    "status": ("status", text),
    "priority": ("priority", text),
    "created_at": ("date", iso),
    "updated_at": ("lastreply", iso),
    "service": ("service", text),
}

_REPLY_MAP = {
    "id": ("replyid", to_int),
    "author": ("name", text),
    "author_type": ("admin", lambda v: "staff" if text(v) else "client"),
    "message": ("message", text),
    "created_at": ("date", iso),
}


class SupportService(OwnedResourceMixin, BaseService):
    cache_resources = ("tickets",)

    def list_departments(self) -> list[dict]:
        def fetch() -> list[dict]:
            data = self.call(Action.GET_SUPPORT_DEPARTMENTS)
            return [
                pick(
                    raw,
                    {
                        "id": ("id", to_int),
                        "name": ("name", text),
                        "awaiting_reply": ("awaitingreply", to_int),
                        "open_tickets": ("opentickets", to_int),
                    },
                )
                for raw in collection(data, "departments", "department")
            ]

        return get_or_set("catalogue:departments", "catalogue", fetch, "list")

    def list_tickets(
        self, whmcs_client_id: int, page: PageRequest, status: str | None = None
    ) -> tuple[list[dict], int]:
        def fetch() -> tuple[list[dict], int]:
            params = {"clientid": whmcs_client_id, **self.page_params(page)}
            if status:
                params["status"] = status
            data = self.call(Action.GET_TICKETS, params)
            items = [pick(raw, _TICKET_MAP) for raw in collection(data, "tickets", "ticket")]
            return items, total_of(data, len(items))

        return get_or_set(
            client_namespace(whmcs_client_id, "tickets"),
            "tickets",
            fetch,
            "list",
            page.page,
            page.page_size,
            status or "all",
        )

    def get_ticket(self, whmcs_client_id: int, ticket_id: int) -> dict:
        data = self.call(Action.GET_TICKET, {"ticketid": ticket_id, "repliessort": "ASC"})
        self.assert_owned(data.get("userid"), whmcs_client_id)

        ticket = pick({**data, "id": data.get("id") or data.get("ticketid")}, _TICKET_MAP)
        ticket["replies"] = [
            pick(raw, _REPLY_MAP) for raw in collection(data, "replies", "reply")
        ]
        return ticket

    # -- writes -----------------------------------------------------------

    def open_ticket(
        self,
        whmcs_client_id: int,
        *,
        department_id: int,
        subject: str,
        message: str,
        priority: str = "Medium",
        service_id: int | None = None,
        client_ip: str = "",
    ) -> dict:
        params = {
            "clientid": whmcs_client_id,
            "deptid": department_id,
            "subject": subject,
            "message": message,
            "priority": priority,
            "markdown": True,
            "clientip": client_ip,
        }
        if service_id:
            params["serviceid"] = service_id

        data = self.call(Action.OPEN_TICKET, params)
        self.invalidate_for(whmcs_client_id, "tickets")
        return {"id": to_int(data.get("id")), "ticket_number": text(data.get("tid"))}

    def reply_to_ticket(
        self, whmcs_client_id: int, ticket_id: int, message: str, client_ip: str = ""
    ) -> dict:
        # Re-read the ticket first: this both validates the id and proves the
        # caller owns it before we write anything.
        self.get_ticket(whmcs_client_id, ticket_id)

        self.call(
            Action.ADD_TICKET_REPLY,
            {
                "ticketid": ticket_id,
                "clientid": whmcs_client_id,
                "message": message,
                "markdown": True,
                "clientip": client_ip,
            },
        )
        self.invalidate_for(whmcs_client_id, "tickets")
        return self.get_ticket(whmcs_client_id, ticket_id)

    def close_ticket(self, whmcs_client_id: int, ticket_id: int) -> dict:
        self.get_ticket(whmcs_client_id, ticket_id)
        self.call(Action.UPDATE_TICKET, {"ticketid": ticket_id, "status": "Closed"})
        self.invalidate_for(whmcs_client_id, "tickets")
        return self.get_ticket(whmcs_client_id, ticket_id)
