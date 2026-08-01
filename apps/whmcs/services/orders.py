"""
Ordering and checkout.

Two rules govern everything here, because this is the only place where the API
can cost the customer money:

1. **The client never sends a price.** WHMCS' ``priceoverride`` family of
   parameters is not reachable from any serializer or from this module - the
   basket is described by *what* is being bought, and WHMCS prices it.
2. **Nothing is provisioned by the bridge.** ``AddOrder`` creates a pending
   order plus an invoice; provisioning happens through WHMCS' own automation
   once that invoice is paid. ``AcceptOrder`` is deliberately never called from
   a customer-facing path.

Payment itself is a redirect to WHMCS' hosted invoice page - no card data ever
reaches this service.
"""

import logging
from typing import Any

from apps.core.cache import client_namespace, get_or_set, invalidate
from apps.core.exceptions import ApplicationError, ResourceNotFound
from apps.core.pagination import PageRequest

from ..actions import Action
from ..normalizers import collection, iso, pick, text, to_int, to_money, total_of
from .base import BaseService, OwnedResourceMixin

logger = logging.getLogger(__name__)

BILLING_CYCLES = (
    "onetime",
    "monthly",
    "quarterly",
    "semiannually",
    "annually",
    "biennially",
    "triennially",
)

DOMAIN_ACTIONS = {"register", "transfer"}

ORDER_STATUSES = frozenset({"Pending", "Active", "Fraud", "Cancelled"})

MAX_ITEMS = 20

_ORDER_MAP = {
    "id": ("id", to_int),
    "order_number": ("ordernum", text),
    "invoice_id": ("invoiceid", to_int),
    "date": ("date", iso),
    "status": ("status", text),
    "payment_status": ("paymentstatus", text),
    "amount": ("amount", to_money),
    "payment_method": ("paymentmethodname", text),
    "promo_code": ("promocode", text),
}

_LINE_ITEM_MAP = {
    "type": ("type", text),
    "relation_id": ("relid", to_int),
    "product_type": ("producttype", text),
    "product": ("product", text),
    "domain": ("domain", text),
    "billing_cycle": ("billingcycle", text),
    "amount": ("amount", to_money),
    "status": ("status", text),
}


class OrderService(OwnedResourceMixin, BaseService):
    cache_resources = ("orders", "invoices", "services", "domains")

    # -- gateways ---------------------------------------------------------

    def list_payment_methods(self) -> list[dict]:
        """The gateways enabled in WHMCS. Also the allowlist a submitted
        ``payment_method`` is checked against - an unknown module would make
        WHMCS fall back to a default gateway silently."""

        def fetch() -> list[dict]:
            data = self.call(Action.GET_PAYMENT_METHODS)
            return [
                {"module": text(raw.get("module")), "name": text(raw.get("displayname"))}
                for raw in collection(data, "paymentmethods", "paymentmethod")
                if raw.get("module")
            ]

        return get_or_set("catalogue:gateways", "catalogue", fetch, "list")

    def is_valid_payment_method(self, module: str) -> bool:
        return any(method["module"] == module for method in self.list_payment_methods())

    # -- placing an order -------------------------------------------------

    def create_order(
        self,
        whmcs_client_id: int,
        *,
        items: list[dict],
        payment_method: str,
        promo_code: str = "",
        nameservers: list[str] | None = None,
        client_ip: str = "",
    ) -> dict:
        """
        Turn a validated basket into a WHMCS order plus its invoice.

        ``items`` are already validated by the serializer. WHMCS reads the basket
        as a set of *parallel arrays* indexed per basket line: ``pid[0]`` is
        priced with ``billingcycle[0]``, and a domain registration on the same
        line uses ``domain[0]``/``domaintype[0]``/``regperiod[0]``. Indices are
        emitted explicitly so a product-only line followed by a domain-only line
        cannot slide into each other.
        """
        if not items:
            raise ApplicationError("The basket is empty.", code="empty_basket")
        if len(items) > MAX_ITEMS:
            raise ApplicationError(
                f"An order may contain at most {MAX_ITEMS} items.", code="basket_too_large"
            )
        if not self.is_valid_payment_method(payment_method):
            raise ApplicationError(
                "Unknown payment method.", code="invalid_payment_method"
            )

        params: dict[str, Any] = {
            "clientid": whmcs_client_id,
            "paymentmethod": payment_method,
            "clientip": client_ip,
            # The customer is buying: they get the invoice and the email.
            "noinvoice": False,
            "noinvoiceemail": False,
            "noemail": False,
        }
        if promo_code:
            params["promocode"] = promo_code
        for index, host in enumerate(nameservers or [], start=1):
            params[f"nameserver{index}"] = host

        indexed: dict[str, dict[int, Any]] = {}

        def put(field: str, index: int, value: Any) -> None:
            indexed.setdefault(field, {})[index] = value

        for index, item in enumerate(items):
            if item["type"] == "product":
                put("pid", index, item["product_id"])
                put("billingcycle", index, item["billing_cycle"])
                put("qty", index, item.get("quantity", 1))
                if item.get("domain"):
                    put("domain", index, item["domain"])
                if item.get("hostname"):
                    put("hostname", index, item["hostname"])
                if item.get("config_options"):
                    put("configoptions", index, base64_serialize(item["config_options"]))
                if item.get("addons"):
                    put("addons", index, ",".join(str(a) for a in item["addons"]))
                if item.get("custom_fields"):
                    put("customfields", index, base64_serialize(item["custom_fields"]))
            else:  # domain
                put("domain", index, item["domain"])
                put("domaintype", index, item["action"])
                put("regperiod", index, item["years"])
                put("idprotection", index, item.get("id_protection", False))
                put("dnsmanagement", index, item.get("dns_management", False))
                put("emailforwarding", index, item.get("email_forwarding", False))
                if item["action"] == "transfer" and item.get("epp_code"):
                    put("eppcode", index, item["epp_code"])

        params.update(indexed)

        data = self.call(Action.ADD_ORDER, params)

        order_id = to_int(data.get("orderid"))
        invoice_id = to_int(data.get("invoiceid"))
        logger.info(
            "order placed: client=%s order=%s invoice=%s items=%s",
            whmcs_client_id,
            order_id,
            invoice_id,
            len(items),
        )

        # The basket touched invoices, services and domains at once.
        self.invalidate_for(whmcs_client_id, *self.cache_resources)

        return {
            "order_id": order_id,
            "invoice_id": invoice_id,
            "service_ids": _id_list(data.get("productids") or data.get("serviceids")),
            "domain_ids": _id_list(data.get("domainids")),
            "addon_ids": _id_list(data.get("addonids")),
            "payment_url": self.invoice_payment_url(invoice_id) if invoice_id else None,
        }

    def invoice_payment_url(self, invoice_id: int) -> str:
        """Checkout continues on WHMCS' hosted invoice page: the gateway form,
        the card fields and any 3-D Secure step stay entirely on their side."""
        cfg = self.client.config
        base = f"{cfg.scheme}://{cfg.netloc}{cfg.base_path}"
        return f"{base}/viewinvoice.php?id={int(invoice_id)}"

    # -- reading ----------------------------------------------------------

    def list_orders(
        self, whmcs_client_id: int, page: PageRequest, status: str | None = None
    ) -> tuple[list[dict], int]:
        def fetch() -> tuple[list[dict], int]:
            params = {"userid": whmcs_client_id, **self.page_params(page)}
            if status in ORDER_STATUSES:
                params["status"] = status
            data = self.call(Action.GET_ORDERS, params)
            items = [self._order(raw) for raw in collection(data, "orders", "order")]
            return items, total_of(data, len(items))

        return get_or_set(
            client_namespace(whmcs_client_id, "orders"),
            "invoices",
            fetch,
            "list",
            page.page,
            page.page_size,
            status or "all",
        )

    def get_order(self, whmcs_client_id: int, order_id: int) -> dict:
        data = self.call(Action.GET_ORDERS, {"id": order_id})
        orders = collection(data, "orders", "order")
        if not orders:
            raise ResourceNotFound("Order not found.")
        raw = orders[0]
        self.assert_owned(raw.get("userid"), whmcs_client_id)
        return self._order(raw)

    def _order(self, raw: dict) -> dict:
        order = pick(raw, _ORDER_MAP)
        order["items"] = [
            pick(line, _LINE_ITEM_MAP)
            for line in collection(raw, "lineitems", "lineitem")
        ]
        if order["invoice_id"]:
            order["payment_url"] = self.invoice_payment_url(order["invoice_id"])
        return order

    def cancel_order(self, whmcs_client_id: int, order_id: int) -> dict:
        """
        A customer may withdraw an order only while it is still pending.

        Anything further along has been paid for or provisioned, and unwinding
        that is a support decision, not a button.
        """
        order = self.get_order(whmcs_client_id, order_id)
        if order["status"] != "Pending":
            raise ApplicationError(
                "Only a pending order can be cancelled. Open a ticket for this one.",
                code="order_not_cancellable",
                details={"status": order["status"]},
            )
        self.call(Action.CANCEL_ORDER, {"orderid": order_id, "cancelsub": False})
        self.invalidate_for(whmcs_client_id, *self.cache_resources)
        invalidate(client_namespace(whmcs_client_id, "orders"))
        return self.get_order(whmcs_client_id, order_id)


def base64_serialize(value: dict) -> str:
    """WHMCS expects config options / custom fields as a base64-encoded PHP
    serialized array."""
    import base64

    parts = []
    for key, raw in value.items():
        item = str(raw)
        parts.append(f'i:{int(key)};s:{len(item.encode())}:"{item}";')
    payload = f"a:{len(value)}:{{{''.join(parts)}}}"
    return base64.b64encode(payload.encode()).decode()


def _id_list(value: Any) -> list[int]:
    """WHMCS returns created ids as a comma separated string."""
    if not value:
        return []
    return [to_int(part) for part in str(value).split(",") if part.strip()]
