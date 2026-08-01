"""End-to-end through DRF with WHMCS mocked at the HTTP boundary."""

import httpx
import pytest
import respx

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db


def _invoice_payload(**overrides):
    return {
        "result": "success",
        "invoiceid": 100,
        "id": 100,
        "userid": 42,
        "status": "Unpaid",
        "date": "2026-07-01",
        "duedate": "2026-07-10",
        "total": "120.00",
        "currencycode": "USD",
        **overrides,
    }


@respx.mock
def test_invoice_list_is_scoped_to_the_authenticated_client(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "totalresults": 1,
                "invoices": {"invoice": [_invoice_payload()]},
            },
        )
    )

    response = auth_client.get("/api/v1/billing/invoices/")

    assert response.status_code == 200
    assert response.json()["results"][0]["total"] == "120.00"
    assert "userid=42" in route.calls[0].request.content.decode()


@respx.mock
def test_invoice_of_another_client_is_404(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_invoice_payload(userid=999))
    )

    response = auth_client.get("/api/v1/billing/invoices/100/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_anonymous_access_is_rejected(client):
    assert client.get("/api/v1/billing/invoices/").status_code == 401


@respx.mock
def test_upstream_timeout_becomes_504(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(side_effect=httpx.ConnectTimeout("timeout"))

    response = auth_client.get("/api/v1/billing/invoices/")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_unreachable"
    # The WHMCS host must never leak into an error shown to the frontend.
    assert "whmcs.test" not in response.content.decode()
