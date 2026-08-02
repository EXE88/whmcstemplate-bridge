"""Support tickets and departments."""

import logging

from apps.core.cache import client_namespace, get_or_set, invalidate
from apps.core.exceptions import ResourceNotFound
from apps.core.pagination import PageRequest

from .. import attachments
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


def _attachment_list(raw: dict, kind: str, related_id: int) -> list[dict]:
    """
    Normalise WHMCS' two shapes for attachment names into one.

    Depending on the call it is either a list under ``attachments`` or a comma
    separated string under ``attachment``. Only names and the index needed to
    fetch them are exposed - the bytes are served by a separate, checked
    endpoint.
    """
    names: list[str] = []
    listed = raw.get("attachments")
    if isinstance(listed, list):
        names = [text(item.get("filename") if isinstance(item, dict) else item) for item in listed]
    elif isinstance(listed, dict):
        inner = listed.get("attachment", [])
        entries = inner if isinstance(inner, list) else [inner]
        names = [text(item.get("filename") if isinstance(item, dict) else item) for item in entries]
    elif raw.get("attachment"):
        names = [part.strip() for part in text(raw["attachment"]).split(",") if part.strip()]

    return [
        {
            "index": index,
            "filename": attachments.safe_filename(name),
            "type": kind,
            "related_id": to_int(related_id),
        }
        for index, name in enumerate(names)
        if name
    ]


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

    def get_ticket(self, whmcs_client_id: int, ticket_id: int, *, fresh: bool = False) -> dict:
        """
        Read one ticket.

        Cached: a WHMCS call costs ~1.1s, and this is read both by the customer
        and as the ownership gate in front of every write. ``fresh=True`` after
        a write, when the caller needs to see its own change.
        """
        namespace = client_namespace(whmcs_client_id, "tickets")
        if fresh:
            invalidate(namespace)
        return get_or_set(
            namespace,
            "tickets",
            lambda: self._fetch_ticket(whmcs_client_id, ticket_id),
            "detail",
            ticket_id,
        )

    def _fetch_ticket(self, whmcs_client_id: int, ticket_id: int) -> dict:
        data = self.call(Action.GET_TICKET, {"ticketid": ticket_id, "repliessort": "ASC"})
        self.assert_owned(data.get("userid"), whmcs_client_id)

        ticket = pick({**data, "id": data.get("id") or data.get("ticketid")}, _TICKET_MAP)
        ticket["attachments"] = _attachment_list(data, "ticket", ticket["id"])
        ticket["replies"] = []
        for raw in collection(data, "replies", "reply"):
            reply = pick(raw, _REPLY_MAP)
            reply["attachments"] = _attachment_list(raw, "reply", reply["id"])
            ticket["replies"].append(reply)
        return ticket

    def get_attachment(
        self, whmcs_client_id: int, ticket_id: int, *, kind: str, related_id: int, index: int
    ) -> tuple[str, bytes]:
        """
        Fetch one stored attachment as ``(filename, bytes)``.

        WHMCS' GetTicketAttachment takes a bare ``relatedid`` and will happily
        return another customer's file, so the request is checked twice: the
        ticket must belong to the caller, and ``related_id`` must be that ticket
        or one of *its* replies. ``note`` is refused outright - notes are the
        staff's internal commentary and are never customer-visible.
        """
        if kind not in ("ticket", "reply"):
            raise ResourceNotFound("Attachment not found.")

        ticket = self.get_ticket(whmcs_client_id, ticket_id)

        permitted = {("ticket", to_int(ticket["id"]))}
        permitted |= {("reply", to_int(reply["id"])) for reply in ticket["replies"]}
        if (kind, int(related_id)) not in permitted:
            logger.warning(
                "attachment denied: client %s asked for %s %s outside ticket %s",
                whmcs_client_id,
                kind,
                related_id,
                ticket_id,
            )
            raise ResourceNotFound("Attachment not found.")

        data = self.call(
            Action.GET_TICKET_ATTACHMENT,
            {"relatedid": related_id, "type": kind, "index": index},
        )
        filename = attachments.safe_filename(text(data.get("filename")))
        return filename, attachments.decode(text(data.get("data")))

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
        files: list[tuple[str, bytes]] | None = None,
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
        if files:
            params["attachments"] = attachments.encode(files)

        data = self.call(Action.OPEN_TICKET, params)
        self.invalidate_for(whmcs_client_id, "tickets")
        return {"id": to_int(data.get("id")), "ticket_number": text(data.get("tid"))}

    def reply_to_ticket(
        self,
        whmcs_client_id: int,
        ticket_id: int,
        message: str,
        client_ip: str = "",
        files: list[tuple[str, bytes]] | None = None,
    ) -> dict:
        # Re-read the ticket first: this both validates the id and proves the
        # caller owns it before we write anything.
        self.get_ticket(whmcs_client_id, ticket_id)

        params = {
            "ticketid": ticket_id,
            "clientid": whmcs_client_id,
            "message": message,
            "markdown": True,
            "clientip": client_ip,
        }
        if files:
            params["attachments"] = attachments.encode(files)

        self.call(Action.ADD_TICKET_REPLY, params)
        return self.get_ticket(whmcs_client_id, ticket_id, fresh=True)

    def close_ticket(self, whmcs_client_id: int, ticket_id: int) -> dict:
        self.get_ticket(whmcs_client_id, ticket_id)
        self.call(Action.UPDATE_TICKET, {"ticketid": ticket_id, "status": "Closed"})
        return self.get_ticket(whmcs_client_id, ticket_id, fresh=True)
