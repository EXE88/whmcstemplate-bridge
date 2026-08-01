"""Invoices, transactions, credit and quotes - always scoped to one client."""

import logging

from django.utils import timezone

from apps.core.cache import client_namespace, get_or_set, invalidate
from apps.core.pagination import PageRequest

from ..actions import Action
from ..normalizers import collection, iso_date, pick, text, to_int, to_money, total_of
from .base import BaseService, OwnedResourceMixin

logger = logging.getLogger(__name__)

INVOICE_STATUSES = frozenset(
    {"Draft", "Unpaid", "Paid", "Overdue", "Cancelled", "Refunded", "Collections"}
)

_INVOICE_LIST_MAP = {
    "id": ("id", to_int),
    "number": ("invoicenum", text),
    "status": ("status", text),
    "date": ("date", iso_date),
    "due_date": ("duedate", iso_date),
    "date_paid": ("datepaid", iso_date),
    "subtotal": ("subtotal", to_money),
    "tax": ("tax", to_money),
    "credit": ("credit", to_money),
    "total": ("total", to_money),
    "currency_code": ("currencycode", text),
    "payment_method": ("paymentmethod", text),
}

_TRANSACTION_MAP = {
    "id": ("id", to_int),
    "invoice_id": ("invoiceid", to_int),
    "date": ("date", iso_date),
    "gateway": ("gateway", text),
    "description": ("description", text),
    "amount_in": ("amountin", to_money),
    "amount_out": ("amountout", to_money),
    "fees": ("fees", to_money),
    "currency": ("currency", text),
    "transaction_id": ("transid", text),
}


class BillingService(OwnedResourceMixin, BaseService):
    cache_resources = ("invoices", "transactions")

    # -- invoices ---------------------------------------------------------

    def list_invoices(
        self,
        whmcs_client_id: int,
        page: PageRequest,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        def fetch() -> tuple[list[dict], int]:
            params = {
                "userid": whmcs_client_id,
                "orderby": "id",
                "order": "desc",
                **self.page_params(page),
            }
            if status in INVOICE_STATUSES:
                params["status"] = status
            data = self.call(Action.GET_INVOICES, params)
            items = [
                pick(raw, _INVOICE_LIST_MAP)
                for raw in collection(data, "invoices", "invoice")
            ]
            return items, total_of(data, len(items))

        return get_or_set(
            client_namespace(whmcs_client_id, "invoices"),
            "invoices",
            fetch,
            "list",
            page.page,
            page.page_size,
            status or "all",
        )

    def get_invoice(self, whmcs_client_id: int, invoice_id: int) -> dict:
        data = self.call(Action.GET_INVOICE, {"invoiceid": invoice_id})
        # WHMCS returns the invoice regardless of who asks - enforce ownership.
        self.assert_owned(data.get("userid"), whmcs_client_id)

        invoice = pick(data, _INVOICE_LIST_MAP)
        # GetInvoice reports the outstanding amount; older versions and the list
        # call do not, in which case the full total is what is due.
        invoice["balance"] = to_money(data.get("balance") or invoice["total"])
        invoice["items"] = [
            pick(
                raw,
                {
                    "id": ("id", to_int),
                    "type": ("type", text),
                    "relation_id": ("relid", to_int),
                    "description": ("description", text),
                    "amount": ("amount", to_money),
                    "taxed": ("taxed", to_int),
                },
            )
            for raw in collection(data, "items", "item")
        ]
        invoice["transactions"] = [
            pick(raw, _TRANSACTION_MAP)
            for raw in collection(data, "transactions", "transaction")
        ]
        invoice["notes"] = text(data.get("notes"))
        return invoice

    def record_payment(
        self,
        whmcs_client_id: int,
        invoice_id: int,
        *,
        transaction_id: str,
        gateway: str,
        amount: str,
        fees: str = "0",
        paid_at: str | None = None,
    ) -> None:
        """
        Mark an invoice as paid.

        This is the moment money becomes real in WHMCS: it settles the invoice
        and lets WHMCS' automation provision the order. It must only ever be
        called after a gateway has confirmed the payment server-to-server -
        never in response to a browser redirect or any client-supplied field.

        ``transaction_id`` is the gateway's own reference. WHMCS rejects a
        duplicate, which is the last line of defence against a replayed
        callback crediting an invoice twice.
        """
        self.call(
            Action.ADD_INVOICE_PAYMENT,
            {
                "invoiceid": invoice_id,
                "transid": transaction_id,
                "gateway": gateway,
                "amount": amount,
                "fees": fees,
                "date": paid_at or timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        self.invalidate_for(whmcs_client_id, "invoices", "transactions")
        invalidate(client_namespace(whmcs_client_id, "services"))
        invalidate(client_namespace(whmcs_client_id, "orders"))
        logger.info(
            "payment recorded: client=%s invoice=%s gateway=%s transid=%s",
            whmcs_client_id,
            invoice_id,
            gateway,
            transaction_id,
        )

    def invoice_payment_url(self, invoice_id: int) -> str:
        """
        Handing off to WHMCS' hosted payment page.

        Card data must never touch this bridge, so checkout redirects to WHMCS
        (or a gateway) instead of proxying payment fields.
        """
        cfg = self.client.config
        base = f"{cfg.scheme}://{cfg.netloc}{cfg.base_path}"
        return f"{base}/viewinvoice.php?id={int(invoice_id)}"

    # -- transactions / credit -------------------------------------------

    def list_transactions(
        self, whmcs_client_id: int, page: PageRequest
    ) -> tuple[list[dict], int]:
        def fetch() -> tuple[list[dict], int]:
            data = self.call(
                Action.GET_TRANSACTIONS,
                {"clientid": whmcs_client_id, **self.page_params(page)},
            )
            items = [
                pick(raw, _TRANSACTION_MAP)
                for raw in collection(data, "transactions", "transaction")
            ]
            return items, total_of(data, len(items))

        return get_or_set(
            client_namespace(whmcs_client_id, "transactions"),
            "invoices",
            fetch,
            "list",
            page.page,
            page.page_size,
        )

    def get_credit_balance(self, whmcs_client_id: int) -> dict:
        """
        GetCredits returns the credit *log*, not a balance, so the authoritative
        figure comes from the client record; the log is exposed alongside it.
        """
        details = self.call(
            Action.GET_CLIENTS_DETAILS, {"clientid": whmcs_client_id, "stats": False}
        )
        client = details.get("client") or details
        log = self.call(Action.GET_CREDITS, {"clientid": whmcs_client_id, "limitnum": 10})
        return {
            "balance": to_money(client.get("credit")),
            "currency_code": text(client.get("currency_code")),
            "entries": [
                pick(
                    raw,
                    {
                        "id": ("id", to_int),
                        "date": ("date", iso_date),
                        "description": ("description", text),
                        "amount": ("amount", to_money),
                    },
                )
                for raw in collection(log, "credits", "credit")
            ],
        }

    def list_pay_methods(self, whmcs_client_id: int) -> list[dict]:
        """Stored payment methods - only masked metadata ever crosses the API."""
        data = self.call(Action.GET_PAY_METHODS, {"clientid": whmcs_client_id})
        return [
            pick(
                raw,
                {
                    "id": ("id", to_int),
                    "type": ("payment_method_type", text),
                    "description": ("description", text),
                    "is_default": ("is_default", to_int),
                },
            )
            for raw in collection(data, "paymethods", "paymethod")
        ]
