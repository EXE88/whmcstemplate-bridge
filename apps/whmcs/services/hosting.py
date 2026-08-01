"""Provisioned services (hosting products) and domains."""

import logging

from apps.core.cache import client_namespace, get_or_set
from apps.core.exceptions import ApplicationError, ResourceNotFound
from apps.core.pagination import PageRequest

from ..actions import Action
from ..normalizers import collection, iso_date, pick, text, to_bool, to_int, to_money, total_of
from .base import BaseService, OwnedResourceMixin

logger = logging.getLogger(__name__)

_SERVICE_MAP = {
    "id": ("id", to_int),
    "product_id": ("pid", to_int),
    "group": ("groupname", text),
    "name": ("name", text),
    "domain": ("domain", text),
    "status": ("status", text),
    "billing_cycle": ("billingcycle", text),
    "amount": ("recurringamount", to_money),
    "first_payment": ("firstpaymentamount", to_money),
    "registered_at": ("regdate", iso_date),
    "next_due_date": ("nextduedate", iso_date),
    "server_hostname": ("serverhostname", text),
    "dedicated_ip": ("dedicatedip", text),
    "username": ("username", text),
    "disk_limit": ("disklimit", text),
    "bandwidth_limit": ("bwlimit", text),
}

_DOMAIN_MAP = {
    "id": ("id", to_int),
    "domain": ("domainname", text),
    "status": ("status", text),
    "registrar": ("registrar", text),
    "registration_period": ("regperiod", to_int),
    "registered_at": ("regdate", iso_date),
    "expires_at": ("expirydate", iso_date),
    "next_due_date": ("nextduedate", iso_date),
    "amount": ("recurringamount", to_money),
    "auto_renew": ("donotrenew", lambda v: not to_bool(v)),
    "id_protection": ("idprotection", to_bool),
}


class ServiceService(OwnedResourceMixin, BaseService):
    cache_resources = ("services",)

    def list_services(
        self, whmcs_client_id: int, page: PageRequest, status: str | None = None
    ) -> tuple[list[dict], int]:
        def fetch() -> tuple[list[dict], int]:
            params = {"clientid": whmcs_client_id, **self.page_params(page)}
            if status:
                params["status"] = status
            data = self.call(Action.GET_CLIENTS_PRODUCTS, params)
            items = [
                pick(raw, _SERVICE_MAP) for raw in collection(data, "products", "product")
            ]
            return items, total_of(data, len(items))

        return get_or_set(
            client_namespace(whmcs_client_id, "services"),
            "client",
            fetch,
            "list",
            page.page,
            page.page_size,
            status or "all",
        )

    def get_service(self, whmcs_client_id: int, service_id: int) -> dict:
        data = self.call(
            Action.GET_CLIENTS_PRODUCTS,
            {"clientid": whmcs_client_id, "serviceid": service_id},
        )
        products = collection(data, "products", "product")
        if not products:
            raise ResourceNotFound("Service not found.")
        raw = products[0]
        # GetClientsProducts is already client-scoped, but assert anyway: the
        # ownership rule lives in one place and is applied uniformly.
        self.assert_owned(raw.get("clientid", whmcs_client_id), whmcs_client_id)
        return pick(raw, _SERVICE_MAP)

    def change_password(self, whmcs_client_id: int, service_id: int, new_password: str) -> None:
        self.get_service(whmcs_client_id, service_id)
        self.call(
            Action.MODULE_CHANGE_PW,
            {"serviceid": service_id, "servicepassword": new_password},
        )
        self.invalidate_for(whmcs_client_id, "services")

    def request_cancellation(
        self,
        whmcs_client_id: int,
        service_id: int,
        *,
        reason: str,
        immediate: bool = False,
    ) -> None:
        self.get_service(whmcs_client_id, service_id)
        self.call(
            Action.ADD_CANCEL_REQUEST,
            {
                "serviceid": service_id,
                "type": "Immediate" if immediate else "End of Billing Period",
                "reason": reason,
            },
        )
        self.invalidate_for(whmcs_client_id, "services")


class DomainService(OwnedResourceMixin, BaseService):
    cache_resources = ("domains",)

    def list_domains(
        self, whmcs_client_id: int, page: PageRequest
    ) -> tuple[list[dict], int]:
        def fetch() -> tuple[list[dict], int]:
            data = self.call(
                Action.GET_CLIENTS_DOMAINS,
                {"clientid": whmcs_client_id, **self.page_params(page)},
            )
            items = [pick(raw, _DOMAIN_MAP) for raw in collection(data, "domains", "domain")]
            return items, total_of(data, len(items))

        return get_or_set(
            client_namespace(whmcs_client_id, "domains"),
            "client",
            fetch,
            "list",
            page.page,
            page.page_size,
        )

    def get_domain(self, whmcs_client_id: int, domain_id: int) -> dict:
        data = self.call(
            Action.GET_CLIENTS_DOMAINS,
            {"clientid": whmcs_client_id, "domainid": domain_id},
        )
        domains = collection(data, "domains", "domain")
        if not domains:
            raise ResourceNotFound("Domain not found.")
        return pick(domains[0], _DOMAIN_MAP)

    def get_nameservers(self, whmcs_client_id: int, domain_id: int) -> list[str]:
        self.get_domain(whmcs_client_id, domain_id)
        data = self.call(Action.DOMAIN_GET_NAMESERVERS, {"domainid": domain_id})
        return [
            text(data.get(f"ns{index}")) for index in range(1, 6) if data.get(f"ns{index}")
        ]

    def update_nameservers(
        self, whmcs_client_id: int, domain_id: int, nameservers: list[str]
    ) -> list[str]:
        self.get_domain(whmcs_client_id, domain_id)
        if not 2 <= len(nameservers) <= 5:
            raise ApplicationError("Provide between 2 and 5 nameservers.")
        params = {"domainid": domain_id}
        params.update({f"ns{i}": ns for i, ns in enumerate(nameservers, start=1)})
        self.call(Action.DOMAIN_UPDATE_NAMESERVERS, params)
        self.invalidate_for(whmcs_client_id, "domains")
        return self.get_nameservers(whmcs_client_id, domain_id)

    def get_lock_status(self, whmcs_client_id: int, domain_id: int) -> bool:
        self.get_domain(whmcs_client_id, domain_id)
        data = self.call(Action.DOMAIN_GET_LOCKING_STATUS, {"domainid": domain_id})
        # WHMCS answers with the string "locked" / "unlocked".
        return text(data.get("lockstatus")).strip().lower() == "locked"

    def set_lock_status(self, whmcs_client_id: int, domain_id: int, locked: bool) -> bool:
        self.get_domain(whmcs_client_id, domain_id)
        self.call(
            Action.DOMAIN_UPDATE_LOCKING_STATUS,
            {"domainid": domain_id, "lockstatus": locked},
        )
        self.invalidate_for(whmcs_client_id, "domains")
        return locked

    def request_epp_code(self, whmcs_client_id: int, domain_id: int) -> dict:
        """
        Some registrars return the code, others email it straight to the domain
        owner and reply with nothing - both are a success, so the caller is told
        which happened instead of receiving an empty string.

        WHMCS may HTML-encode the code for display; decode before use.
        """
        import html

        self.get_domain(whmcs_client_id, domain_id)
        data = self.call(Action.DOMAIN_REQUEST_EPP, {"domainid": domain_id})
        code = html.unescape(text(data.get("eppcode"))).strip()
        return {
            "epp_code": code or None,
            "delivery": "inline" if code else "emailed_to_owner",
        }

    def whois(self, domain: str) -> dict:
        """Availability lookup - slow and heavily hit, so it is cached."""

        def fetch() -> dict:
            data = self.call(Action.DOMAIN_WHOIS, {"domain": domain})
            return {
                "domain": domain,
                "available": text(data.get("status")).lower() == "available",
                "whois": text(data.get("whois"))[:4000],
            }

        return get_or_set("catalogue:whois", "catalogue", fetch, domain.lower())
