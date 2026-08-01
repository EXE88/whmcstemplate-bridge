"""Checkout: the only path that can cost the customer money."""

import httpx
import pytest
import respx

from .conftest import WHMCS_ENDPOINT

pytestmark = pytest.mark.django_db

GATEWAYS = {
    "result": "success",
    "totalresults": 2,
    "paymentmethods": {
        "paymentmethod": [
            {"module": "banktransfer", "displayname": "Bank Transfer"},
            {"module": "zarinpal", "displayname": "ZarinPal"},
        ]
    },
}

ORDER_OK = {"result": "success", "orderid": 12, "invoiceid": 34, "serviceids": "56"}


def _basket(**overrides):
    return {
        "payment_method": "zarinpal",
        "items": [
            {
                "type": "product",
                "product_id": 5,
                "billing_cycle": "annually",
                "domain": "example.ir",
            },
            {"type": "domain", "domain": "example.ir", "action": "register", "years": 2},
        ],
        **overrides,
    }


@respx.mock
def test_order_is_placed_with_parallel_arrays_and_the_session_client(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=GATEWAYS),
            httpx.Response(200, json=ORDER_OK),
        ]
    )

    response = auth_client.post("/api/v1/orders/", data=_basket(), format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["order_id"] == 12
    assert body["invoice_id"] == 34
    assert body["payment_url"].endswith("/viewinvoice.php?id=34")

    sent = route.calls[1].request.content.decode()
    assert "clientid=42" in sent
    # Line 0 is the product, line 1 the domain - indices must not collide.
    assert "pid%5B0%5D=5" in sent
    assert "billingcycle%5B0%5D=annually" in sent
    assert "domaintype%5B1%5D=register" in sent
    assert "regperiod%5B1%5D=2" in sent


@respx.mock
def test_prices_cannot_be_dictated_by_the_client(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=GATEWAYS),
            httpx.Response(200, json=ORDER_OK),
        ]
    )

    basket = _basket()
    basket["items"][0]["priceoverride"] = "0.01"
    basket["priceoverride"] = "0.01"
    basket["noinvoice"] = True

    response = auth_client.post("/api/v1/orders/", data=basket, format="json")

    assert response.status_code == 201
    sent = route.calls[1].request.content.decode()
    assert "priceoverride" not in sent
    assert "noinvoice=0" in sent  # forced off, never taken from the request


@respx.mock
def test_unknown_payment_method_is_rejected_before_reaching_whmcs(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(return_value=httpx.Response(200, json=GATEWAYS))

    response = auth_client.post(
        "/api/v1/orders/", data=_basket(payment_method="free-money"), format="json"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_payment_method"
    assert route.call_count == 1  # only the gateway lookup


@respx.mock
def test_idempotency_key_prevents_a_double_purchase(auth_client):
    route = respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=GATEWAYS),
            httpx.Response(200, json=ORDER_OK),
        ]
    )

    first = auth_client.post(
        "/api/v1/orders/", data=_basket(), format="json", HTTP_IDEMPOTENCY_KEY="abc-123"
    )
    second = auth_client.post(
        "/api/v1/orders/", data=_basket(), format="json", HTTP_IDEMPOTENCY_KEY="abc-123"
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["order_id"] == first.json()["order_id"]
    # AddOrder was called exactly once.
    assert route.call_count == 2


@respx.mock
def test_a_lost_response_does_not_allow_a_silent_second_purchase(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=GATEWAYS),
            httpx.ConnectTimeout("connection lost after the request was sent"),
            httpx.Response(200, json=GATEWAYS),
        ]
    )

    first = auth_client.post(
        "/api/v1/orders/", data=_basket(), format="json", HTTP_IDEMPOTENCY_KEY="key-1"
    )
    retry = auth_client.post(
        "/api/v1/orders/", data=_basket(), format="json", HTTP_IDEMPOTENCY_KEY="key-1"
    )

    assert first.status_code == 400
    assert first.json()["error"]["code"] == "order_outcome_unknown"
    # The same key cannot quietly place a second order.
    assert retry.json()["error"]["code"] == "order_in_progress"


def test_transfer_requires_an_epp_code(auth_client):
    response = auth_client.post(
        "/api/v1/orders/",
        data={
            "payment_method": "zarinpal",
            "items": [{"type": "domain", "domain": "example.ir", "action": "transfer"}],
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


@respx.mock
def test_order_of_another_client_is_404(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "totalresults": 1,
                "orders": {"order": [{"id": 12, "userid": 999, "status": "Pending"}]},
            },
        )
    )

    assert auth_client.get("/api/v1/orders/12/").status_code == 404


@respx.mock
def test_only_a_pending_order_can_be_cancelled(auth_client):
    respx.post(WHMCS_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "success",
                "totalresults": 1,
                "orders": {"order": [{"id": 12, "userid": 42, "status": "Active"}]},
            },
        )
    )

    response = auth_client.post("/api/v1/orders/12/cancel/", format="json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "order_not_cancellable"
