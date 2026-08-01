"""
Response-shape tests against the payloads WHMCS 8.x actually returns.

These pin the awkward spots: the ticket id has a different name in the list and
detail calls, GetCredits has no balance field, and TLD pricing nests years under
each operation.
"""

import httpx
import pytest
import respx

from apps.core.pagination import PageRequest
from apps.whmcs.services import BillingService, CatalogueService, SupportService

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db


@respx.mock
def test_ticket_detail_id_comes_from_ticketid():
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "ticketid": 7,
                "tid": "123456",
                "userid": 42,
                "deptname": "Support",
                "subject": "Disk full",
                "status": "Open",
                "date": "2026-07-30 10:00:00",
                "lastreply": "2026-07-31 09:00:00",
                "replies": {"reply": [{"replyid": 1, "name": "Ali", "message": "hi"}]},
            },
        )
    )

    ticket = SupportService().get_ticket(42, 7)

    assert ticket["id"] == 7
    assert ticket["ticket_number"] == "123456"
    assert ticket["replies"][0]["author_type"] == "client"


@respx.mock
def test_credit_balance_uses_the_client_record_not_the_log():
    respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "result": "success",
                    "client": {"id": 42, "credit": "25.50", "currency_code": "IRR"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "result": "success",
                    "totalresults": 1,
                    "credits": {
                        "credit": [
                            {
                                "id": 3,
                                "date": "2026-07-01",
                                "amount": "25.50",
                                "description": "top-up",
                            }
                        ]
                    },
                },
            ),
        ]
    )

    balance = BillingService().get_credit_balance(42)

    assert balance["balance"] == "25.50"
    assert balance["currency_code"] == "IRR"
    assert balance["entries"][0]["amount"] == "25.50"


@respx.mock
def test_tld_pricing_exposes_first_year_prices():
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "pricing": {
                    "com": {
                        "categories": ["Popular"],
                        "register": {"1": "14.95", "2": "29.90"},
                        "transfer": {"1": "14.95"},
                        "renew": {"1": "16.95"},
                        "grace_period": None,
                        "redemption_period": None,
                    },
                    "xyz": {"categories": [], "register": {}},
                },
            },
        )
    )

    pricing = CatalogueService().tld_pricing()

    assert pricing["com"]["register"] == "14.95"
    assert pricing["com"]["renew"] == "16.95"
    # No configured price means "not offered", not free.
    assert pricing["xyz"]["register"] is None


@respx.mock
def test_invoice_list_pagination_maps_to_limitstart_limitnum():
    route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"result": "success", "totalresults": 0, "invoices": {"invoice": []}}
        )
    )

    BillingService().list_invoices(42, PageRequest(page=3, page_size=20))

    body = route.calls[0].request.content.decode()
    assert "limitstart=40" in body
    assert "limitnum=20" in body
